"""Injection de pannes contrôlées, uniquement pour les démonstrations d'incident (désactivée par défaut).

BANK_FAULT_PUBLISH_FAILURES=N : les N premiers appels de publication MLflow échouent avec une erreur de connexion
transitoire, puis les suivants réussissent. Le compteur est un fichier : il survit aux retries Prefect.
"""
import os
from pathlib import Path

COUNTER = Path(__file__).resolve().parents[1] / 'data' / 'state' / 'fault_publish_count'


def maybe_fail_publication():
    planned = int(os.environ.get('BANK_FAULT_PUBLISH_FAILURES', '0') or 0)
    if planned <= 0:
        return
    COUNTER.parent.mkdir(parents=True, exist_ok=True)
    done = int(COUNTER.read_text()) if COUNTER.exists() else 0
    if done < planned:
        COUNTER.write_text(str(done + 1))
        raise ConnectionError(f'Injection de panne {done + 1}/{planned} : connexion au serveur MLflow refusée '
                              '(panne transitoire simulée)')


def reset():
    if COUNTER.exists():
        COUNTER.unlink()
