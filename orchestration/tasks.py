"""Les quatre fonctions mères exposées comme tâches Prefect (logs contextualisés, sans retry ni cache).

Politique de reprise :
- validation des données en échec → échec explicite, jamais de retry (une non-conformité ne se corrige pas seule) ;
- publication MLflow → seule étape réessayée (2 retries sur erreur transitoire, idempotente par run_id),
  voir src/evaluation.py ;
- évaluation / entraînement → pas de retry automatique : un nouveau calcul est un nouvel entraînement.
"""
from prefect import get_run_logger, task
from prefect.cache_policies import NO_CACHE

from src.evaluation import evaluate_models
from src.ingestion import load_data
from src.preprocessing import preprocess_data
from src.training import train_models

COMMON = {'retries': 0, 'cache_policy': NO_CACHE, 'persist_result': False}


def _tag_flow_run(loaded):
    """Rendre le lot et le run MLflow visibles dans Prefect (tags du flow run, utilisés par l'alerte)."""
    if not loaded.flow_run_id:
        return
    try:
        from prefect.client.orchestration import get_client
        with get_client(sync_client=True) as client:
            flow_run = client.read_flow_run(loaded.flow_run_id)
            batch = (loaded.batch or {}).get('key', 'corpus-initial')
            tags = sorted(set(flow_run.tags) | {f'batch:{batch}', f'mlflow-run:{loaded.mlflow_run_id}'})
            client.update_flow_run(loaded.flow_run_id, tags=tags)
    except Exception as exc:  # noqa: BLE001 - confort de lecture, pas une condition de succès
        get_run_logger().warning('Tags du flow run non mis à jour : %s', exc)


@task(name='ingestion', timeout_seconds=600, **COMMON)
def ingestion(config_path, batch='next', trigger='manual'):
    logger = get_run_logger()
    logger.info('Ingestion : config=%s, lot demandé=%s, déclencheur=%s', config_path, batch, trigger)
    loaded = load_data(config_path, batch=batch, trigger=trigger)
    _tag_flow_run(loaded)
    logger.info('Lot %s validé ; snapshot %s lignes (sha %s) ; run MLflow %s',
                (loaded.batch or {}).get('key', 'aucun'), len(loaded.snapshot), loaded.snapshot_sha256[:12],
                loaded.mlflow_run_id)
    return loaded


@task(name='pretraitement', timeout_seconds=600, **COMMON)
def pretraitement(loaded):
    prepared = preprocess_data(loaded)
    get_run_logger().info('Split stratifié : %s train, %s test, %s qualification (taux positifs train %.3f)',
                          len(prepared.X_train), len(prepared.X_test), len(prepared.X_qualification),
                          prepared.y_train.mean())
    return prepared


@task(name='entrainement', timeout_seconds=1200, **COMMON)
def entrainement(prepared):
    trained = train_models(prepared)
    get_run_logger().info('Régression logistique entraînée en %.2f s', trained.training_seconds)
    return trained


@task(name='evaluation-publication', timeout_seconds=900, **COMMON)
def evaluation(trained):
    summary = evaluate_models(trained)
    get_run_logger().info('Version %s du modèle %s : qualification=%s, sélection=%s, AUC qualif=%.4f',
                          summary['version'], summary['model'], summary['qualification'],
                          summary['selection'], summary['metrics']['qualification.roc_auc'])
    return summary
