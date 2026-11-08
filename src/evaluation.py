"""Étape 4 — évaluation et publication : métriques, modèle MLflow rechargé, qualification, sélection.

Ordre des effets : calculs → publication de la version (idempotente) → contrôle de rechargement →
décision de qualification (tags) → sélection éventuelle (alias) → lot inscrit comme accepté → run fermé.
Une panne avant la publication ne laisse aucune version ; une panne après laisse une version identifiable,
taguée « pending », retrouvée par son run_id lors d'une reprise (pas de doublon).
"""
import json
import logging
import math
import os
import time

import numpy as np
from sklearn.metrics import (
    accuracy_score,
    average_precision_score,
    brier_score_loss,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)

from src import ledger, pipeline_events, registry, tracking
from src.config import path
from src.contract import NUMERIC_FEATURES
from src.qualification import decide, load_policy, should_replace_champion
from src.training import TrainedModels

logger = logging.getLogger(__name__)
TRANSIENT_MARKERS = ('connection', 'max retries', 'timed out', 'timeout', '503', '502', 'temporarily')


def classification_metrics(y_true, proba, threshold):
    predicted = (proba >= threshold).astype(int)
    prevalence = float(np.mean(y_true))
    roc = float(roc_auc_score(y_true, proba)) if len(set(y_true)) > 1 else float('nan')
    pr = float(average_precision_score(y_true, proba)) if len(set(y_true)) > 1 else float('nan')
    return {
        'roc_auc': roc, 'pr_auc': pr, 'prevalence': prevalence,
        'pr_auc_lift': pr / prevalence if prevalence > 0 else float('nan'),
        'precision': float(precision_score(y_true, predicted, zero_division=0)),
        'recall': float(recall_score(y_true, predicted, zero_division=0)),
        'f1': float(f1_score(y_true, predicted, zero_division=0)),
        'accuracy': float(accuracy_score(y_true, predicted)),
        'brier': float(brier_score_loss(y_true, proba)), 'n': int(len(y_true)),
        'predicted_positive_rate': float(predicted.mean()),
    }


def _is_transient(exc):
    message = f'{type(exc).__name__} {exc}'.lower()
    return isinstance(exc, (ConnectionError, TimeoutError)) or any(m in message for m in TRANSIENT_MARKERS)


def _retry_if_transient(task, task_run, state):
    try:
        state.result()
    except Exception as exc:  # noqa: BLE001
        return _is_transient(exc)
    return False


def _publish(run_id, model, example, name, tags):
    """Dans un flow : tâche Prefect avec 2 retries sur erreur transitoire ; hors flow : appel direct."""
    try:
        from prefect.context import FlowRunContext
        in_flow = FlowRunContext.get() is not None
    except Exception:  # noqa: BLE001
        in_flow = False
    if not in_flow:
        return registry.publish_model(run_id, model, example, name, tags)
    from prefect import task
    from prefect.cache_policies import NO_CACHE
    publish_task = task(registry.publish_model, name='publication-mlflow', retries=2, retry_delay_seconds=5,
                        retry_condition_fn=_retry_if_transient, cache_policy=NO_CACHE, persist_result=False)
    return publish_task(run_id, model, example, name, tags)


def _example(X):
    example = X.head(5).copy()
    for column in NUMERIC_FEATURES:
        example[column] = example[column].astype('float64')
    return example


def evaluate_models(trained):
    prepared = trained.prepared
    loaded = prepared.loaded
    config = loaded.config
    run_id = loaded.mlflow_run_id
    threshold = float(config['decision_threshold'])
    policy = load_policy(path(config, 'policy'))
    name = config['mlflow']['registered_model']

    proba_test = trained.model.predict_proba(prepared.X_test)[:, 1]
    proba_qual = trained.model.predict_proba(prepared.X_qualification)[:, 1]
    test = classification_metrics(prepared.y_test.to_numpy(), proba_test, threshold)
    qual = classification_metrics(prepared.y_qualification.to_numpy(), proba_qual, threshold)
    base_qual = classification_metrics(prepared.y_qualification.to_numpy(),
                                       trained.baseline.predict_proba(prepared.X_qualification)[:, 1], threshold)
    qual['roc_auc_gain_vs_baseline'] = qual['roc_auc'] - base_qual['roc_auc']
    metrics = {**{f'test.{k}': v for k, v in test.items()}, **{f'qualification.{k}': v for k, v in qual.items()},
               **{f'baseline.qualification.{k}': v for k, v in base_qual.items()}}
    tracking.log_metrics(run_id, {k: v for k, v in metrics.items() if math.isfinite(v)})

    # Publication du run : dossier temporaire puis renommage (jamais de dossier partiel « publié »).
    tmp_dir = loaded.run_dir.with_name(loaded.run_dir.name + '.tmp')
    tmp_dir.mkdir(parents=True, exist_ok=False)
    (tmp_dir / 'metrics.json').write_text(json.dumps(metrics, indent=2), encoding='utf-8')

    publication_tags = {'qualification': 'pending', 'run_name': loaded.run_name,
                        'batch_id': (loaded.batch or {}).get('key', 'corpus-initial'),
                        'snapshot_sha256': loaded.snapshot_sha256,
                        'qualification_corpus_sha256': loaded.qualification_sha256,
                        'prefect_flow_run_id': loaded.flow_run_id or 'hors-prefect'}
    example = _example(prepared.X_qualification)
    published = _publish(run_id, trained.model, example, name, publication_tags)
    version = published['version']
    tracking.update_open_run(loaded.execution_id, stage='qualification', model_version=version)

    # Contrôle technique : recharger depuis MLflow et comparer aux prédictions en mémoire.
    import mlflow
    tracking.configure()
    reloaded = mlflow.sklearn.load_model(f'models:/{name}/{version}')
    reload_gap = float(np.max(np.abs(reloaded.predict_proba(prepared.X_qualification)[:, 1] - proba_qual)))
    provenance = path(config, 'source_provenance')
    expected_source = json.loads(provenance.read_text(encoding='utf-8'))['sha256'] if provenance.exists() else None
    manifest = path(config, 'base') / 'data' / 'prepared' / 'manifest.json'
    protocol_sha = json.loads(manifest.read_text(encoding='utf-8'))['qualification']['sha256'] \
        if manifest.exists() else None
    technical = {
        'metrics_finite': all(math.isfinite(v) for k, v in metrics.items() if k.startswith('qualification.')),
        'reload_identical': reload_gap <= 1e-9,
        'qualification_corpus_matches': protocol_sha == loaded.qualification_sha256,
        'provenance_real': expected_source is not None,
    }
    decision = decide(metrics, technical, policy)
    decision.update({'model': name, 'version': version, 'mlflow_run_id': run_id, 'reload_max_gap': reload_gap})
    stamp = decision['decided_at'][:19].replace(':', '').replace('-', '')
    tracking.log_dict(run_id, decision, f'qualification/decision_v{version}_{stamp}.json')
    registry.set_version_tags(name, version, {
        'qualification': decision['status'], 'qualification_policy': policy['policy_id'],
        'qualification_reason': decision['reason'][:4000], 'qualification_at': decision['decided_at'],
        'qualification.roc_auc': f"{qual['roc_auc']:.6f}", 'qualification.recall': f"{qual['recall']:.6f}",
    })

    selection = {'selected': False, 'reason': 'qualification non satisfaite'}
    if decision['status'] == 'production-ready' and config.get('auto_select', False):
        champion = registry.get_alias_version(name, policy['selection']['alias'])
        champion_auc = None
        if champion is not None:
            tags = champion.tags or {}
            if tags.get('qualification_corpus_sha256') == loaded.qualification_sha256:
                champion_auc = float(tags.get('qualification.roc_auc', 'nan'))
        replace, why = should_replace_champion(qual['roc_auc'], champion_auc, policy)
        if replace:
            entry = registry.select(name, version, policy, reason=f'sélection automatique : {why}',
                                    actor='pipeline', qualification_sha=loaded.qualification_sha256)
            selection = {'selected': True, 'reason': why, 'previous_version': entry['previous_version']}
        else:
            selection = {'selected': False, 'reason': why}
    registry.set_version_tags(name, version, {'selection': ('selected: ' if selection['selected'] else
                                                            'not-selected: ') + selection['reason']})

    if loaded.batch:
        ledger.mark_accepted(path(config, 'ledger'), loaded.batch['key'], source=loaded.batch['source'],
                             sha256=loaded.batch['sha256'], archive_dir=path(config, 'accepted_dir'),
                             mlflow_run_id=run_id, flow_run_id=loaded.flow_run_id, model_version=version)

    summary = {
        'run_name': loaded.run_name, 'mlflow_run_id': run_id, 'model': name, 'version': version,
        'qualification': decision['status'], 'qualification_reason': decision['reason'],
        'selection': selection, 'batch': loaded.batch, 'snapshot_rows': len(loaded.snapshot),
        'metrics': {k: metrics[k] for k in ('qualification.roc_auc', 'qualification.pr_auc',
                                            'qualification.recall', 'test.roc_auc')},
        'duration_s': round(time.monotonic() - loaded.started_monotonic, 2),
    }
    (tmp_dir / 'decision.json').write_text(json.dumps(decision, ensure_ascii=False, indent=2), encoding='utf-8')
    (tmp_dir / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str),
                                          encoding='utf-8')
    os.replace(tmp_dir, loaded.run_dir)
    tracking.log_dict(run_id, summary, 'summary.json')
    tracking.set_tags(run_id, {'final_status': 'succeeded', 'qualification': decision['status'],
                               'model_version': version, 'selected': selection['selected']})
    tracking.end_run(run_id, 'FINISHED')
    tracking.forget_open_run(loaded.execution_id)
    pipeline_events.record('completed', loaded.execution_id, flow_run_id=loaded.flow_run_id,
                           trigger=loaded.trigger, batch_id=(loaded.batch or {}).get('key'),
                           rows_in_batch=(loaded.batch or {}).get('rows'), rows_in_snapshot=len(loaded.snapshot),
                           mlflow_run_id=run_id, model_version=version, qualification=decision['status'],
                           selected=selection['selected'], duration_s=summary['duration_s'])
    logger.info('Version %s publiée : %s ; sélection : %s', version, decision['status'], selection)
    return summary


__all__ = ['evaluate_models', 'classification_metrics', 'TrainedModels']
