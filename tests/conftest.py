import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
os.environ['PIPELINE_EVENTS_DISABLED'] = '1'


@pytest.fixture
def fake_tracking(monkeypatch, tmp_path):
    """Remplacer MLflow par un enregistreur en mémoire : les tests unitaires n'ont pas besoin du serveur."""
    from src import tracking
    calls = {'tags': {}, 'params': {}, 'metrics': {}, 'artifacts': [], 'ended': []}
    monkeypatch.setattr(tracking, 'STATE_DIR', tmp_path / 'active_runs')
    monkeypatch.setattr(tracking, 'start_run', lambda *a, **k: 'run-test')
    monkeypatch.setattr(tracking, 'log_params', lambda run_id, p: calls['params'].update(p))
    monkeypatch.setattr(tracking, 'log_metrics', lambda run_id, m: calls['metrics'].update(m))
    monkeypatch.setattr(tracking, 'set_tags', lambda run_id, t: calls['tags'].update(t))
    monkeypatch.setattr(tracking, 'log_dict', lambda run_id, d, f: calls['artifacts'].append(f))
    monkeypatch.setattr(tracking, 'log_artifact', lambda run_id, p, a=None: calls['artifacts'].append(str(p)))
    monkeypatch.setattr(tracking, 'end_run', lambda run_id, status='FINISHED': calls['ended'].append(status))
    import src.preprocessing as preprocessing
    monkeypatch.setattr(preprocessing, '_log_inputs', lambda run_id, prepared: None)
    return calls
