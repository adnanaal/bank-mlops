"""Incident contrôlé « dépendance indisponible » : arrêt de PostgreSQL pendant que l'API sert.

Attendu : /health/ready → 503, /predict → 503 storage_unavailable (aucun faux succès, diagnostic local),
puis retour à la normale après redémarrage. L'API doit tourner sur 127.0.0.1:8001.

Usage : uv run --locked python scripts/demo_dependency_outage.py
"""
import json
import subprocess
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
API = 'http://127.0.0.1:8001'
CLIENT = json.loads((ROOT / 'data' / 'loadtest' / 'clients.json').read_text(encoding='utf-8'))[0]
HEADERS = {'X-Test-Campaign': 'demo-incident-postgres'}


def step(title):
    print(f'\n=== {title}')


def call():
    ready = httpx.get(f'{API}/health/ready', timeout=10)
    predict = httpx.post(f'{API}/predict', json={'client': CLIENT}, headers=HEADERS, timeout=15)
    print(f'/health/ready -> {ready.status_code} {ready.json()}')
    print(f'/predict      -> {predict.status_code} {predict.json()}')
    return ready.status_code, predict.status_code


def compose(*args):
    subprocess.run(['docker', 'compose', *args], cwd=ROOT, check=True)


def main():
    step('1. Fonctionnement nominal')
    assert call() == (200, 200)
    step('2. Arrêt de PostgreSQL (docker compose stop postgres)')
    compose('stop', 'postgres')
    time.sleep(2)
    ready, predict = call()
    assert (ready, predict) == (503, 503), 'Une panne de stockage doit donner 503, jamais un faux succès'
    log = ROOT / 'data' / 'state' / 'api_trace_failures.jsonl'
    if log.exists():
        print('Diagnostic local (dernière ligne) :', log.read_text(encoding='utf-8').splitlines()[-1][:300])
    step('3. Redémarrage de PostgreSQL et reprise')
    compose('start', 'postgres')
    for _ in range(30):
        time.sleep(2)
        try:
            if httpx.get(f'{API}/health/ready', timeout=5).status_code == 200:
                break
        except httpx.HTTPError:
            pass
    assert call() == (200, 200)
    print('\nIncident maîtrisé : erreurs explicites pendant la panne, reprise sans redémarrer l\'API.')


if __name__ == '__main__':
    main()
