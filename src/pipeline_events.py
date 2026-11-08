"""Journal des traitements dans PostgreSQL (tracing.pipeline_events), lu par le dashboard Grafana.

Écriture best-effort : une panne de la base de supervision est journalisée localement mais ne fait
pas échouer un entraînement par ailleurs valide (limite documentée : trou possible dans le dashboard).
"""
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_DSN = 'postgresql://api_writer:5mlde-local-api@127.0.0.1:15432/bank_api'
FALLBACK_LOG = Path(__file__).resolve().parents[1] / 'data' / 'state' / 'pipeline_events_fallback.jsonl'
logger = logging.getLogger(__name__)


def record(event, execution_id, **fields):
    row = {
        'event_id': str(uuid.uuid4()), 'event': event, 'execution_id': str(execution_id),
        'occurred_at': datetime.now(timezone.utc).isoformat(), **fields,
    }
    if os.environ.get('PIPELINE_EVENTS_DISABLED') == '1':
        return row
    try:
        import psycopg
        with psycopg.connect(os.environ.get('PIPELINE_DB_DSN', DEFAULT_DSN), connect_timeout=3) as conn:
            conn.execute(
                """INSERT INTO tracing.pipeline_events
                   (event_id, occurred_at, event, execution_id, flow_run_id, trigger, batch_id, rows_in_batch,
                    rows_in_snapshot, mlflow_run_id, model_version, qualification, selected, failed_stage,
                    error, duration_s)
                   VALUES (%(event_id)s, %(occurred_at)s, %(event)s, %(execution_id)s, %(flow_run_id)s,
                    %(trigger)s, %(batch_id)s, %(rows_in_batch)s, %(rows_in_snapshot)s, %(mlflow_run_id)s,
                    %(model_version)s, %(qualification)s, %(selected)s, %(failed_stage)s, %(error)s,
                    %(duration_s)s)""",
                {key: row.get(key) for key in [
                    'event_id', 'occurred_at', 'event', 'execution_id', 'flow_run_id', 'trigger', 'batch_id',
                    'rows_in_batch', 'rows_in_snapshot', 'mlflow_run_id', 'model_version', 'qualification',
                    'selected', 'failed_stage', 'error', 'duration_s']})
    except Exception as exc:  # noqa: BLE001 - supervision best-effort
        logger.warning('Événement pipeline non écrit en base (%s) ; journal local utilisé', exc)
        FALLBACK_LOG.parent.mkdir(parents=True, exist_ok=True)
        with FALLBACK_LOG.open('a', encoding='utf-8') as handle:
            handle.write(json.dumps({**row, 'db_error': str(exc)[:300]}, ensure_ascii=False) + '\n')
    return row
