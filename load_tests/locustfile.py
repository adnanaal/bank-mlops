"""Scénarios Locust : unitaire, lot de 20 clients, mélange réaliste avec feedbacks.

Les clients envoyés sont construits à l'avance depuis le corpus de qualification (aucune donnée générée à la volée,
pas d'appel externe). Chaque réponse est vérifiée : un 200 au corps invalide compte comme un échec.

Variables : LOCUST_SCENARIO = unit | batch20 | mixed ; LOCUST_CAMPAIGN = identifiant transmis dans X-Test-Campaign.
  uv run --locked locust -f load_tests/locustfile.py --host http://127.0.0.1:8001 --headless -u 20 -r 20 -t 60s
"""
import json
import os
import random
import uuid
from datetime import datetime, timezone
from pathlib import Path

from locust import HttpUser, between, events, task

ROOT = Path(__file__).resolve().parents[1]
CLIENTS_FILE = ROOT / 'data' / 'loadtest' / 'clients.json'
SCENARIO = os.environ.get('LOCUST_SCENARIO', 'mixed')
CAMPAIGN = os.environ.get('LOCUST_CAMPAIGN', f'locust-{SCENARIO}')[:128]
BATCH_SIZE = 20
CLIENTS = []


@events.init.add_listener
def load_clients(environment, **kwargs):
    if not CLIENTS_FILE.exists():
        raise SystemExit(f'{CLIENTS_FILE} absent : lancer scripts/prepare_loadtest_data.py')
    CLIENTS.extend(json.loads(CLIENTS_FILE.read_text(encoding='utf-8')))


def _valid_prediction(p):
    return (0.0 <= p.get('probability', -1) <= 1.0 and p.get('label') in ('yes', 'no')
            and len(str(p.get('prediction_id', ''))) == 36)


class BankClient(HttpUser):
    wait_time = between(0.05, 0.2)

    def on_start(self):
        self.client.headers.update({'X-Test-Campaign': CAMPAIGN})
        self.rng = random.Random()
        self.last_predictions = []

    def _unit(self):
        payload = {'client': self.rng.choice(CLIENTS)}
        with self.client.post('/predict', json=payload, name='/predict', catch_response=True) as response:
            if response.status_code != 200:
                response.failure(f'statut {response.status_code}')
                return
            body = response.json()
            if not _valid_prediction(body.get('prediction', {})) or 'version' not in body.get('model', {}):
                response.failure('corps invalide')
                return
            self.last_predictions.append(body['prediction']['prediction_id'])

    def _batch(self):
        payload = {'clients': self.rng.sample(CLIENTS, BATCH_SIZE)}
        with self.client.post('/predict/batch', json=payload, name='/predict/batch [20]',
                              catch_response=True) as response:
            if response.status_code != 200:
                response.failure(f'statut {response.status_code}')
                return
            predictions = response.json().get('predictions', [])
            if len(predictions) != BATCH_SIZE or not all(_valid_prediction(p) for p in predictions) \
                    or [p['item_index'] for p in predictions] != list(range(BATCH_SIZE)):
                response.failure('lot incomplet ou invalide')
                return
            self.last_predictions.extend(p['prediction_id'] for p in predictions[:2])

    def _feedback(self):
        if not self.last_predictions:
            return
        payload = {'feedback_id': str(uuid.uuid4()), 'prediction_id': self.last_predictions.pop(0),
                   'useful': self.rng.random() < 0.7, 'observed_label': self.rng.choice(['yes', 'no']),
                   'observed_at': datetime.now(timezone.utc).isoformat(), 'label_source': 'loadtest'}
        with self.client.post('/feedback', json=payload, name='/feedback', catch_response=True) as response:
            if response.status_code != 201 or response.json().get('status') != 'created':
                response.failure(f'statut {response.status_code}')

    @task
    def scenario(self):
        if SCENARIO == 'unit':
            self._unit()
        elif SCENARIO == 'batch20':
            self._batch()
        else:  # mélange : 70 % unitaire, 20 % lot, 10 % feedback
            draw = self.rng.random()
            if draw < 0.7:
                self._unit()
            elif draw < 0.9:
                self._batch()
            else:
                self._feedback()
