"""Helpers MLflow : un run par tentative de pipeline, piloté par un run_id explicite.

Le run est ouvert par l'ingestion, enrichi par chaque étape et fermé par l'évaluation (succès)
ou par le hook d'échec du flow. Un fichier d'état local garde le run_id ouvert pour cette finalisation
et pour réconcilier après une panne (voir find_open_runs).
"""
import json
import os
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_TRACKING_URI = 'http://localhost:7281'
STATE_DIR = Path(__file__).resolve().parents[1] / 'data' / 'state' / 'active_runs'


def configure():
    import mlflow
    # Échouer vite si le serveur est indisponible (le client MLflow réessaie sinon plusieurs minutes) :
    # les reprises sont gérées par Prefect sur la seule tâche de publication.
    os.environ.setdefault('MLFLOW_HTTP_REQUEST_MAX_RETRIES', '2')
    os.environ.setdefault('MLFLOW_HTTP_REQUEST_BACKOFF_FACTOR', '1')
    os.environ.setdefault('MLFLOW_HTTP_REQUEST_TIMEOUT', '20')
    uri = os.environ.get('MLFLOW_TRACKING_URI', DEFAULT_TRACKING_URI)
    mlflow.set_tracking_uri(uri)
    mlflow.set_registry_uri(os.environ.get('MLFLOW_REGISTRY_URI', uri))
    return uri


def client():
    from mlflow import MlflowClient
    configure()
    return MlflowClient()


def code_version():
    root = Path(__file__).resolve().parents[1]
    try:
        commit = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=root, capture_output=True, text=True,
                                timeout=10).stdout.strip() or 'no-commit'
        dirty = bool(subprocess.run(['git', 'status', '--porcelain'], cwd=root, capture_output=True,
                                    text=True, timeout=10).stdout.strip())
    except (OSError, subprocess.SubprocessError):
        commit, dirty = 'git-indisponible', True
    return {'git_commit': commit, 'git_dirty': str(dirty).lower()}


def start_run(experiment_name, run_name, tags):
    import mlflow
    configure()
    experiment = mlflow.set_experiment(experiment_name)
    base_tags = {
        'python': sys.version.split()[0], 'platform': platform.platform(),
        'started_at_utc': datetime.now(timezone.utc).isoformat(), **code_version(),
    }
    run = client().create_run(experiment.experiment_id, run_name=run_name,
                              tags={k: str(v) for k, v in {**base_tags, **tags}.items()})
    return run.info.run_id


def log_params(run_id, params):
    c = client()
    for key, value in params.items():
        c.log_param(run_id, key, str(value)[:6000])


def log_metrics(run_id, metrics):
    c = client()
    for key, value in metrics.items():
        if value is not None:
            c.log_metric(run_id, key, float(value))


def set_tags(run_id, tags):
    c = client()
    for key, value in tags.items():
        c.set_tag(run_id, key, str(value))


def log_dict(run_id, payload, artifact_file):
    client().log_dict(run_id, payload, artifact_file)


def log_artifact(run_id, path, artifact_path=None):
    client().log_artifact(run_id, str(path), artifact_path)


def end_run(run_id, status='FINISHED'):
    client().set_terminated(run_id, status=status)


# --- État local des runs ouverts (finalisation par hook, réconciliation après panne) ---

def remember_open_run(execution_id, payload):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    (STATE_DIR / f'{execution_id}.json').write_text(json.dumps(payload, indent=2), encoding='utf-8')


def update_open_run(execution_id, **fields):
    path = STATE_DIR / f'{execution_id}.json'
    payload = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    payload.update(fields)
    remember_open_run(execution_id, payload)
    return payload


def read_open_run(execution_id):
    path = STATE_DIR / f'{execution_id}.json'
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else None


def forget_open_run(execution_id):
    path = STATE_DIR / f'{execution_id}.json'
    if path.exists():
        done = STATE_DIR.parent / 'finished_runs'
        done.mkdir(parents=True, exist_ok=True)
        os.replace(path, done / path.name)


def find_open_runs():
    return [json.loads(p.read_text(encoding='utf-8')) for p in sorted(STATE_DIR.glob('*.json'))] \
        if STATE_DIR.exists() else []


def finalize_failed(execution_id, error_message, stage=None):
    """Fermer en FAILED le run MLflow d'une exécution en échec, en conservant la cause."""
    state = read_open_run(execution_id)
    if not state or not state.get('mlflow_run_id'):
        return None
    run_id = state['mlflow_run_id']
    try:
        set_tags(run_id, {'final_status': 'failed', 'failed_stage': stage or state.get('stage', 'inconnue'),
                          'error': str(error_message)[:4000]})
        end_run(run_id, 'FAILED')
        forget_open_run(execution_id)
    except Exception as exc:  # noqa: BLE001 - MLflow peut être la cause de la panne
        update_open_run(execution_id, reconciliation_needed=True, finalize_error=str(exc)[:500])
    return run_id
