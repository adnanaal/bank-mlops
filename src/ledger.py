"""Registre durable des lots reçus : statut, empreinte, run associé.

Un lot est identifié par son nom de fichier ET son empreinte : un lot rejeté n'est jamais rejoué tel quel,
mais une version corrigée (empreinte différente) peut être traitée et reste traçable.
Seuls les rejets métier (validation) et les publications réussies sont inscrits : un échec technique
(MLflow indisponible, etc.) laisse le lot en attente pour une reprise.
"""
import hashlib
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path


def file_sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def batch_key(path, sha=None):
    sha = sha or file_sha256(path)
    return f'{Path(path).stem}:{sha[:12]}'


def load(ledger_path):
    path = Path(ledger_path)
    if not path.exists():
        return {'batches': {}}
    return json.loads(path.read_text(encoding='utf-8'))


def _save(ledger_path, ledger):
    path = Path(ledger_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(ledger, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(tmp, path)


def pending_batches(landing_dir, ledger_path):
    """Fichiers de la zone d'arrivée jamais traités avec cette empreinte, du plus ancien au plus récent."""
    ledger = load(ledger_path)
    landing = Path(landing_dir)
    if not landing.exists():
        return []
    return [p for p in sorted(landing.glob('*.csv')) if batch_key(p) not in ledger['batches']]


def accepted_batches(ledger_path):
    ledger = load(ledger_path)
    entries = [e for e in ledger['batches'].values() if e['status'] == 'accepted']
    return sorted(entries, key=lambda e: e['decided_at'])


def mark_rejected(ledger_path, key, *, source, sha256, report, mlflow_run_id, flow_run_id):
    ledger = load(ledger_path)
    ledger['batches'][key] = {
        'key': key, 'source': str(source), 'sha256': sha256, 'status': 'rejected',
        'decided_at': datetime.now(timezone.utc).isoformat(), 'validation_report': str(report),
        'mlflow_run_id': mlflow_run_id, 'flow_run_id': flow_run_id,
    }
    _save(ledger_path, ledger)


def mark_accepted(ledger_path, key, *, source, sha256, archive_dir, mlflow_run_id, flow_run_id, model_version):
    """Archiver une copie immuable du lot accepté puis l'inscrire comme intégré au corpus."""
    archive = Path(archive_dir)
    archive.mkdir(parents=True, exist_ok=True)
    stored = archive / f"{key.replace(':', '_')}.csv"
    if not stored.exists():
        shutil.copyfile(source, stored)
    if file_sha256(stored) != sha256:
        raise RuntimeError(f'Copie archivée du lot {key} différente du lot validé')
    ledger = load(ledger_path)
    ledger['batches'][key] = {
        'key': key, 'source': str(source), 'archived': str(stored), 'sha256': sha256, 'status': 'accepted',
        'decided_at': datetime.now(timezone.utc).isoformat(), 'mlflow_run_id': mlflow_run_id,
        'flow_run_id': flow_run_id, 'model_version': model_version,
    }
    _save(ledger_path, ledger)
