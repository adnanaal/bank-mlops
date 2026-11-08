"""Model Registry MLflow : publication idempotente, tags de version, alias champion et retour arrière."""
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from src import tracking
from src.qualification import load_policy

HISTORY = Path(__file__).resolve().parents[1] / 'data' / 'state' / 'selection_history.jsonl'


class SelectionRefused(Exception):
    """La version demandée ne satisfait pas les conditions de sélection."""


def find_version_for_run(name, run_id):
    """Reprise : une version déjà enregistrée pour ce run n'est jamais dupliquée."""
    c = tracking.client()
    try:
        versions = c.search_model_versions(f"name='{name}' and run_id='{run_id}'")
    except Exception:  # noqa: BLE001 - modèle encore jamais enregistré
        return None
    return versions[0] if versions else None


def publish_model(run_id, model, example, registered_name, tags):
    """Journaliser le pipeline complet dans le run puis l'enregistrer comme nouvelle version."""
    import mlflow
    from mlflow.models import infer_signature

    from src.faults import maybe_fail_publication
    maybe_fail_publication()
    existing = find_version_for_run(registered_name, run_id)
    if existing is not None:
        return {'version': int(existing.version), 'model_uri': existing.source, 'reused': True}
    tracking.configure()
    signature = infer_signature(example, model.predict_proba(example)[:, 1])
    with mlflow.start_run(run_id=run_id):
        info = mlflow.sklearn.log_model(
            model, name='model', signature=signature, input_example=example,
            serialization_format='cloudpickle', code_paths=[str(Path(__file__).resolve().parent)],
            pip_requirements=['scikit-learn==1.6.1', 'pandas==2.2.3', 'numpy==2.2.6', 'cloudpickle'])
    version = mlflow.register_model(info.model_uri, registered_name, tags={k: str(v) for k, v in tags.items()})
    return {'version': int(version.version), 'model_uri': info.model_uri, 'reused': False}


def set_version_tags(name, version, tags):
    c = tracking.client()
    for key, value in tags.items():
        c.set_model_version_tag(name, str(version), key, str(value))


def get_alias_version(name, alias):
    c = tracking.client()
    try:
        return c.get_model_version_by_alias(name, alias)
    except Exception:  # noqa: BLE001 - alias absent
        return None


def _append_history(entry):
    HISTORY.parent.mkdir(parents=True, exist_ok=True)
    with HISTORY.open('a', encoding='utf-8') as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + '\n')


def read_history(name=None, alias=None):
    if not HISTORY.exists():
        return []
    rows = [json.loads(line) for line in HISTORY.read_text(encoding='utf-8').splitlines() if line.strip()]
    return [r for r in rows if (name is None or r['model'] == name) and (alias is None or r['alias'] == alias)]


def check_eligible(name, version, policy, qualification_sha):
    """Une version n'est sélectionnable que si sa qualification a été calculée selon la politique courante."""
    c = tracking.client()
    mv = c.get_model_version(name, str(version))
    tags = mv.tags or {}
    problems = []
    if tags.get('qualification') != 'production-ready':
        problems.append(f"qualification = {tags.get('qualification')!r}")
    if tags.get('qualification_policy') != policy['policy_id']:
        problems.append(f"politique {tags.get('qualification_policy')!r} != {policy['policy_id']!r}")
    if qualification_sha and tags.get('qualification_corpus_sha256') != qualification_sha:
        problems.append('corpus de qualification différent du protocole courant')
    if problems:
        raise SelectionRefused(f'Version {version} non sélectionnable : ' + ' ; '.join(problems))
    return mv


def select(name, version, policy, *, reason, actor=None, qualification_sha=None, alias=None):
    alias = alias or policy['selection']['alias']
    mv = check_eligible(name, version, policy, qualification_sha)
    previous = get_alias_version(name, alias)
    c = tracking.client()
    c.set_registered_model_alias(name, alias, str(version))
    entry = {
        'at': datetime.now(timezone.utc).isoformat(), 'model': name, 'alias': alias,
        'previous_version': int(previous.version) if previous else None, 'new_version': int(version),
        'actor': actor or os.environ.get('USERNAME') or os.environ.get('USER') or 'inconnu',
        'reason': reason, 'policy_id': policy['policy_id'], 'run_id': mv.run_id,
        'qualification_roc_auc': (mv.tags or {}).get('qualification.roc_auc'),
    }
    _append_history(entry)
    c.set_registered_model_tag(name, f'{alias}.last_change', json.dumps(entry, ensure_ascii=False)[:4900])
    return entry


def rollback(name, policy, *, reason='retour arrière', actor=None, alias=None):
    """Remettre l'alias sur la version qui le portait avant la sélection courante."""
    alias = alias or policy['selection']['alias']
    current = get_alias_version(name, alias)
    if current is None:
        raise SelectionRefused(f"Aucune version ne porte l'alias {alias}")
    history = [h for h in read_history(name, alias) if h['new_version'] == int(current.version)]
    if not history or history[-1]['previous_version'] is None:
        raise SelectionRefused('Aucune version précédente connue pour ce retour arrière')
    target = history[-1]['previous_version']
    c = tracking.client()
    c.set_registered_model_alias(name, alias, str(target))
    entry = {
        'at': datetime.now(timezone.utc).isoformat(), 'model': name, 'alias': alias,
        'previous_version': int(current.version), 'new_version': int(target),
        'actor': actor or os.environ.get('USERNAME') or 'inconnu', 'reason': reason,
        'policy_id': policy['policy_id'], 'run_id': c.get_model_version(name, str(target)).run_id,
        'rollback': True,
    }
    _append_history(entry)
    c.set_registered_model_tag(name, f'{alias}.last_change', json.dumps(entry, ensure_ascii=False)[:4900])
    return entry


def policy_from_config(config):
    return load_policy(config['_paths']['policy'])
