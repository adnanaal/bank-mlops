"""Déploiements locaux Prefect (flow.serve) : surveillance planifiée de la zone d'arrivée + lancement manuel.

- bank-batch-watcher / planifie : toutes les N secondes, traite le prochain lot en attente (un par passage)
  en appelant le flow d'entraînement comme sous-flow. Aucun lot → exécution courte et réussie.
- bank-training-pipeline / manuel : lancement depuis l'interface avec le paramètre config_path.

Usage (processus qui reste actif ; Ctrl+C pour l'arrêter, ce qui met le planning en pause) :
  uv run --locked python orchestration/deploy.py --interval 120
"""
import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from prefect import flow, get_run_logger, serve  # noqa: E402

from main import main as training_pipeline  # noqa: E402
from src import ledger  # noqa: E402
from src.config import load_config, path  # noqa: E402

DEFAULT_CONFIG = str(ROOT / 'configs' / 'config.json')


@flow(name='bank-batch-watcher', retries=0)
def watch_landing(config_path: str = DEFAULT_CONFIG):
    logger = get_run_logger()
    config = load_config(config_path)
    pending = ledger.pending_batches(path(config, 'landing_dir'), path(config, 'ledger'))
    if not pending:
        logger.info('Aucun lot en attente dans %s', path(config, 'landing_dir'))
        return {'processed': None, 'pending': 0}
    batch = pending[0]
    logger.info('Lot détecté : %s (%s autre(s) en attente)', batch.name, len(pending) - 1)
    summary = training_pipeline(config_path, batch=str(batch), trigger='schedule')
    return {'processed': batch.name, 'pending': len(pending) - 1, 'version': summary['version'],
            'qualification': summary['qualification'], 'selected': summary['selection']['selected']}


def code_version():
    try:
        return subprocess.run(['git', 'rev-parse', '--short', 'HEAD'], cwd=ROOT, capture_output=True,
                              text=True, timeout=10).stdout.strip() or 'dev'
    except OSError:
        return 'dev'


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--interval', type=int, default=120, help='Période du planning en secondes')
    parser.add_argument('--config', default=DEFAULT_CONFIG, help='Chemin absolu de la configuration')
    args = parser.parse_args()
    config_path = str(Path(args.config).resolve())
    version = code_version()
    serve(
        watch_landing.to_deployment(
            name='planifie', interval=args.interval, parameters={'config_path': config_path},
            description="Traite le prochain lot déposé dans data/landing (rejeu mensuel de Bank Marketing).",
            version=version, tags=['bank-mlops']),
        training_pipeline.to_deployment(
            name='manuel', parameters={'config_path': config_path, 'batch': 'next', 'trigger': 'ui'},
            description="Entraînement complet sur le prochain lot (ou batch='none' pour le corpus initial).",
            version=version, tags=['bank-mlops']),
        limit=1,
    )
