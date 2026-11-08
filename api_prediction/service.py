"""Ressources d'un worker : modèle chargé une fois depuis MLflow (version précise) et pool PostgreSQL."""
import json
import logging
import os
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DSN = 'postgresql://api_writer:5mlde-local-api@127.0.0.1:15432/bank_api'
LOCAL_ERROR_LOG = ROOT / 'data' / 'state' / 'api_trace_failures.jsonl'
logger = logging.getLogger('api_prediction')


class StorageUnavailable(Exception):
    """PostgreSQL indisponible ou transaction annulée."""


def api_revision():
    revision = os.environ.get('API_REVISION')
    if revision:
        return revision
    try:
        return subprocess.run(['git', 'rev-parse', '--short', 'HEAD'], cwd=ROOT, capture_output=True, text=True,
                              timeout=5).stdout.strip() or 'dev'
    except OSError:
        return 'dev'


@dataclass
class Service:
    manifest: dict
    model: object
    pool: object
    instance_id: uuid.UUID = field(default_factory=uuid.uuid4)
    worker_pid: int = field(default_factory=os.getpid)
    revision: str = field(default_factory=api_revision)
    model_id: uuid.UUID | None = None

    @property
    def threshold(self):
        return float(self.manifest['decision_threshold'])

    def identity(self):
        return {'registered_name': self.manifest['registered_name'], 'version': self.manifest['version'],
                'mlflow_run_id': self.manifest['mlflow_run_id']}

    # --- Base de données ---

    def ensure_model_id(self, conn):
        """Retrouver ou créer la ligne model_versions (unicité tracking_uri, nom, version)."""
        if self.model_id is not None:
            return self.model_id
        m = self.manifest
        row = conn.execute(
            """INSERT INTO tracing.model_versions (model_id, tracking_uri, registered_name, version, mlflow_run_id,
                   model_uri, qualification) VALUES (%s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (tracking_uri, registered_name, version) DO NOTHING RETURNING model_id""",
            (uuid.uuid4(), m['tracking_uri'], m['registered_name'], m['version'], m['mlflow_run_id'],
             m['model_uri'], m.get('qualification'))).fetchone()
        if row is None:
            row = conn.execute(
                """SELECT model_id, mlflow_run_id FROM tracing.model_versions
                   WHERE tracking_uri = %s AND registered_name = %s AND version = %s""",
                (m['tracking_uri'], m['registered_name'], m['version'])).fetchone()
            if row[1] != m['mlflow_run_id']:
                raise RuntimeError('Version MLflow déjà enregistrée avec un autre run : manifeste incohérent')
        self.model_id = row[0]
        return self.model_id

    def db_ready(self):
        try:
            with self.pool.connection(timeout=2) as conn:
                conn.execute('SELECT 1')
                self.ensure_model_id(conn)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning('PostgreSQL indisponible : %s', exc)
            return False


def load_manifest(path=None):
    path = Path(path or os.environ.get('SERVICE_MANIFEST', ROOT / 'configs' / 'service_manifest.json'))
    manifest = json.loads(path.read_text(encoding='utf-8'))
    required = ['registered_name', 'version', 'mlflow_run_id', 'model_uri', 'tracking_uri', 'decision_threshold']
    missing = [k for k in required if k not in manifest]
    if missing:
        raise RuntimeError(f'Manifeste incomplet : {missing}')
    if manifest.get('qualification') != 'production-ready' and os.environ.get('ALLOW_UNQUALIFIED') != '1':
        raise RuntimeError(f"Version {manifest['version']} non qualifiée ({manifest.get('qualification')}) : "
                           'refus de servir (ALLOW_UNQUALIFIED=1 pour une démonstration identifiée)')
    return manifest


def load_model(manifest):
    import mlflow
    os.environ.setdefault('MLFLOW_HTTP_REQUEST_MAX_RETRIES', '2')
    os.environ.setdefault('MLFLOW_HTTP_REQUEST_TIMEOUT', '20')
    mlflow.set_tracking_uri(manifest['tracking_uri'])
    mlflow.set_registry_uri(manifest['tracking_uri'])
    started = time.perf_counter()
    model = mlflow.sklearn.load_model(manifest['model_uri'])
    logger.info('Modèle %s v%s chargé en %.1f s', manifest['registered_name'], manifest['version'],
                time.perf_counter() - started)
    return model


def open_pool():
    from psycopg_pool import ConnectionPool
    pool = ConnectionPool(os.environ.get('API_DB_DSN', DEFAULT_DSN), min_size=1,
                          max_size=int(os.environ.get('API_DB_POOL_MAX', '5')), timeout=5, open=False,
                          kwargs={'connect_timeout': 3})
    pool.open(wait=False)
    return pool


def log_local_failure(payload):
    """Dernier recours si la trace ne peut pas être écrite en base (trou documenté dans le dashboard)."""
    LOCAL_ERROR_LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOCAL_ERROR_LOG.open('a', encoding='utf-8') as handle:
        handle.write(json.dumps({'at': datetime.now(timezone.utc).isoformat(), **payload}, default=str) + '\n')
