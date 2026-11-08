"""Étape 1 — ingestion : choisir le lot, ouvrir le run MLflow, valider (GX) puis construire le snapshot.

Ordre : lot entrant → validation stricte du lot → snapshot candidat (initial + lots acceptés + lot)
→ validation du snapshot → écriture immuable → retour. Aucun prétraitement n'a lieu avant ces contrôles.
"""
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from src import ledger, pipeline_events, tracking
from src.config import load_config, path, public_view
from src.contract import ID_COLUMN, PERIOD_COLUMN, TARGET, NoPendingBatchError, RunDirectoryExistsError
from src.data_validation import (
    ValidationBlockedError,
    aptitude_table,
    batch_quality_table,
    frame_sha256,
    to_csv_bytes,
    validate,
)

logger = logging.getLogger(__name__)


@dataclass
class LoadedData:
    config: dict
    execution_id: str
    flow_run_id: str | None
    trigger: str
    mlflow_run_id: str
    run_name: str
    run_dir: Path
    snapshot: pd.DataFrame
    snapshot_path: Path
    snapshot_sha256: str
    qualification: pd.DataFrame
    qualification_sha256: str
    batch: dict | None
    validation_reports: list = field(default_factory=list)
    started_monotonic: float = 0.0


def current_flow_run_id():
    try:
        from prefect.runtime import flow_run
        return str(flow_run.id) if flow_run.id else None
    except Exception:  # noqa: BLE001 - hors Prefect
        return None


def _select_batch(config, batch):
    if batch in (None, 'none'):
        return None
    if batch == 'next':
        pending = ledger.pending_batches(path(config, 'landing_dir'), path(config, 'ledger'))
        if not pending:
            raise NoPendingBatchError(f"Aucun lot en attente dans {path(config, 'landing_dir')}")
        return pending[0]
    candidate = Path(batch)
    if not candidate.is_absolute():
        candidate = path(config, 'base') / candidate
    if not candidate.exists():
        raise FileNotFoundError(f'Lot introuvable : {candidate}')
    return candidate


def _read_accepted(config):
    frames = []
    for entry in ledger.accepted_batches(path(config, 'ledger')):
        archived = Path(entry['archived'])
        if ledger.file_sha256(archived) != entry['sha256']:
            raise RuntimeError(f"Lot accepté altéré sur disque : {entry['key']}")
        frames.append(pd.read_csv(archived))
    return frames


def load_data(config_path, batch='next', trigger='manual'):
    """Lire la configuration et les données, valider avec GX et retourner le snapshot validé."""
    import time
    started = time.monotonic()
    config = load_config(config_path)
    flow_run_id = current_flow_run_id()
    execution_id = flow_run_id or f'local-{uuid.uuid4()}'
    batch_path = _select_batch(config, batch)
    batch_sha = ledger.file_sha256(batch_path) if batch_path else None
    batch_key = ledger.batch_key(batch_path, batch_sha) if batch_path else None
    now = datetime.now(timezone.utc)
    run_name = f"{now:%Y%m%dT%H%M%S}_{batch_path.stem if batch_path else 'initial'}"
    run_dir = path(config, 'output_root') / run_name
    if run_dir.exists():
        raise RunDirectoryExistsError(f'Dossier de run déjà existant : {run_dir}')

    run_id = tracking.start_run(config['mlflow']['experiment'], run_name, {
        'trigger': trigger, 'execution_id': execution_id, 'prefect_flow_run_id': flow_run_id or 'hors-prefect',
        'batch_id': batch_key or 'aucun (corpus initial)', 'data_source': 'uci-bank-additional-full',
        'qualification': 'pending',
    })
    tracking.remember_open_run(execution_id, {
        'mlflow_run_id': run_id, 'execution_id': execution_id, 'flow_run_id': flow_run_id, 'trigger': trigger,
        'batch_id': batch_key, 'stage': 'ingestion', 'started_at': now.isoformat(), 'run_name': run_name})
    pipeline_events.record('started', execution_id, flow_run_id=flow_run_id, trigger=trigger,
                           batch_id=batch_key, mlflow_run_id=run_id)
    tracking.log_dict(run_id, public_view(config), 'config/config_resolue.json')
    policy_path = path(config, 'policy')
    tracking.log_artifact(run_id, policy_path, 'config')
    provenance_path = path(config, 'source_provenance')
    if provenance_path.exists():
        tracking.log_artifact(run_id, provenance_path, 'data')

    quality = config['quality']
    diagnostics = path(config, 'diagnostics_dir')
    initial = pd.read_csv(path(config, 'initial'))
    qualification = pd.read_csv(path(config, 'qualification'))
    accepted = _read_accepted(config)
    known_ids = set(initial[ID_COLUMN]).union(*[set(f[ID_COLUMN]) for f in accepted])
    reports = []

    batch_info = None
    frames = [initial, *accepted]
    if batch_path is not None:
        batch_frame = pd.read_csv(batch_path)
        batch_info = {'key': batch_key, 'source': str(batch_path), 'sha256': batch_sha,
                      'rows': int(len(batch_frame)),
                      'period': str(batch_frame[PERIOD_COLUMN].iloc[0]) if len(batch_frame) else None}
        audit = batch_quality_table(batch_frame, quality, known_ids, set(qualification[ID_COLUMN]))
        try:
            report_path, _ = validate(
                [('bank_lot', 'lot entrant', batch_frame), ('bank_lot_qualite', 'audit qualité du lot', audit)],
                stage='lot', source=batch_key, snapshot_bytes=batch_path.read_bytes(),
                diagnostics_dir=diagnostics, quality=quality, extra={'mlflow_run_id': run_id})
        except ValidationBlockedError as exc:
            ledger.mark_rejected(path(config, 'ledger'), batch_key, source=batch_path, sha256=batch_sha,
                                 report=exc.report_path, mlflow_run_id=run_id, flow_run_id=flow_run_id)
            tracking.log_artifact(run_id, exc.report_path, 'validation')
            tracking.set_tags(run_id, {'validation': 'rejected', 'batch_status': 'rejected'})
            tracking.update_open_run(execution_id, stage='ingestion/validation-lot')
            raise
        reports.append(str(report_path))
        frames.append(batch_frame)

    snapshot = pd.concat(frames, ignore_index=True)
    snapshot_bytes = to_csv_bytes(snapshot)
    report_path, _ = validate(
        [('bank_snapshot', 'snapshot complet', snapshot),
         ('bank_aptitude', 'aptitude à entraîner', aptitude_table(snapshot, config['test_size']))],
        stage='snapshot', source=batch_key or 'corpus-initial', snapshot_bytes=snapshot_bytes,
        diagnostics_dir=diagnostics, quality=quality, extra={'mlflow_run_id': run_id})
    reports.append(str(report_path))

    snapshot_path = path(config, 'snapshots_dir') / f'{run_name}.csv'
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot_path.write_bytes(snapshot_bytes)
    snapshot_sha = frame_sha256(snapshot)
    qualification_sha = ledger.file_sha256(path(config, 'qualification'))
    for report in reports:
        tracking.log_artifact(run_id, report, 'validation')
    tracking.log_artifact(run_id, snapshot_path, 'data')
    sources = {'initial': str(path(config, 'initial')), 'accepted_batches': [
        e['key'] for e in ledger.accepted_batches(path(config, 'ledger'))], 'new_batch': batch_info}
    tracking.log_dict(run_id, sources, 'data/sources.json')
    tracking.log_params(run_id, {
        'data.snapshot_sha256': snapshot_sha, 'data.qualification_sha256': qualification_sha,
        'data.snapshot_rows': len(snapshot), 'data.accepted_batches': len(accepted),
        'data.new_batch_rows': batch_info['rows'] if batch_info else 0,
    })
    tracking.set_tags(run_id, {'validation': 'passed', 'snapshot_sha256': snapshot_sha,
                               'qualification_corpus_sha256': qualification_sha})
    tracking.update_open_run(execution_id, stage='preprocessing', rows_in_snapshot=len(snapshot),
                             rows_in_batch=batch_info['rows'] if batch_info else 0)
    logger.info('Snapshot validé : %s lignes (%s), lot %s', len(snapshot), snapshot_sha[:12], batch_key)
    return LoadedData(
        config=config, execution_id=execution_id, flow_run_id=flow_run_id, trigger=trigger,
        mlflow_run_id=run_id, run_name=run_name, run_dir=run_dir, snapshot=snapshot,
        snapshot_path=snapshot_path, snapshot_sha256=snapshot_sha, qualification=qualification,
        qualification_sha256=qualification_sha, batch=batch_info, validation_reports=reports,
        started_monotonic=started)


def describe(loaded):
    return json.dumps({'run': loaded.run_name, 'rows': len(loaded.snapshot),
                       'positive_rate': float((loaded.snapshot[TARGET] == 'yes').mean())})
