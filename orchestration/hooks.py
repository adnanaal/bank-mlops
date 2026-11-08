"""Hooks d'état du flow : finaliser le run MLflow et journaliser l'échec pour le dashboard.

L'alerte Teams n'est PAS envoyée ici : elle passe par une automation Prefect côté serveur
(orchestration/setup_alerting.py), qui fonctionne même si le processus du flow a disparu.
"""
from src import pipeline_events, tracking


def _failure(flow, flow_run, state, event):
    execution_id = str(flow_run.id)
    open_run = tracking.read_open_run(execution_id) or {}
    message = state.message or str(state.type)
    stage = open_run.get('stage', 'inconnue')
    run_id = tracking.finalize_failed(execution_id, message, stage=stage)
    params = dict(getattr(flow_run, 'parameters', None) or {})  # échec avant l'ouverture du run MLflow
    pipeline_events.record(event, execution_id, flow_run_id=execution_id,
                           trigger=open_run.get('trigger') or params.get('trigger'),
                           batch_id=open_run.get('batch_id') or params.get('batch'), rows_in_batch=open_run.get('rows_in_batch'),
                           rows_in_snapshot=open_run.get('rows_in_snapshot'), mlflow_run_id=run_id,
                           model_version=open_run.get('model_version'), failed_stage=stage,
                           error=message[:2000])


def on_failure(flow, flow_run, state):
    _failure(flow, flow_run, state, 'failed')


def on_crashed(flow, flow_run, state):
    _failure(flow, flow_run, state, 'crashed')
