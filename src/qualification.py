"""Politique de qualification versionnée : fonction de décision pure, testable, sans effet MLflow.

La décision consomme des résultats d'évaluation déjà calculés ; elle ne réentraîne rien.
"""
import json
import math
import operator
from datetime import datetime, timezone
from pathlib import Path

OPERATORS = {'>=': operator.ge, '>': operator.gt, '<=': operator.le, '<': operator.lt}
TECHNICAL_LABELS = {
    'metrics_finite': 'Toutes les métriques sont finies',
    'reload_identical': 'Le modèle rechargé depuis MLflow prédit à l\'identique',
    'qualification_corpus_matches': 'Corpus de qualification identique au protocole (même empreinte)',
    'provenance_real': 'Entraîné sur les données réelles UCI (pas de démonstration)',
}


def load_policy(policy_path):
    return json.loads(Path(policy_path).read_text(encoding='utf-8'))


def _finite(value):
    return isinstance(value, (int, float)) and math.isfinite(value)


def decide(metrics, technical, policy):
    """Retourne {'status', 'checks', ...} ; status ∈ production-ready / rejected / demo-only."""
    checks = []
    for check in policy['checks']:
        observed = metrics.get(check['metric'])
        if not _finite(observed):
            checks.append({**check, 'observed': observed, 'passed': False,
                           'motif': 'métrique absente ou non finie'})
            continue
        passed = OPERATORS[check['op']](observed, check['value'])
        checks.append({**check, 'observed': observed, 'passed': bool(passed),
                       'motif': 'ok' if passed else check['reason']})
    for name in policy['technical_checks']:
        passed = technical.get(name) is True
        checks.append({'id': name, 'metric': None, 'op': None, 'value': True, 'observed': technical.get(name),
                       'passed': passed, 'motif': 'ok' if passed else TECHNICAL_LABELS.get(name, name)})
    failed = [c for c in checks if not c['passed']]
    if technical.get('provenance_real') is False:
        status = 'demo-only'
    else:
        status = 'rejected' if failed else 'production-ready'
    return {
        'policy_id': policy['policy_id'], 'decided_at': datetime.now(timezone.utc).isoformat(),
        'status': status, 'checks': checks,
        'reason': 'tous les contrôles satisfaits' if not failed else '; '.join(
            f"{c['id']}: {c['motif']}" for c in failed),
    }


def should_replace_champion(candidate_auc, champion_auc, policy):
    """Règle de mise en service : non-infériorité par rapport au champion actuel."""
    if champion_auc is None:
        return True, 'aucun champion actuel'
    margin = float(policy['selection']['non_inferiority_margin'])
    if candidate_auc >= champion_auc - margin:
        return True, f'AUC {candidate_auc:.4f} >= champion {champion_auc:.4f} - {margin}'
    return False, f'AUC {candidate_auc:.4f} < champion {champion_auc:.4f} - {margin}'
