"""Tests de contrat de l'API sur la vraie base PostgreSQL (ignorés si elle est indisponible).

Le modèle MLflow est remplacé par un pipeline entraîné à la volée : on teste le service, pas le registre.
"""
import os
import uuid

import pytest
from helpers import make_frame

from src.contract import FEATURES
from src.training import build_model

DSN = os.environ.get('API_DB_DSN', 'postgresql://api_writer:5mlde-local-api@127.0.0.1:15432/bank_api')
CAMPAIGN = f'pytest-{uuid.uuid4().hex[:8]}'


def _db_available():
    try:
        import psycopg
        with psycopg.connect(DSN, connect_timeout=2) as conn:
            conn.execute('SELECT 1 FROM tracing.api_requests LIMIT 1')
        return True
    except Exception:  # noqa: BLE001
        return False


pytestmark = pytest.mark.skipif(not _db_available(), reason='PostgreSQL du projet indisponible')

CLIENT = {
    'client_ref': 'test-1', 'age': 41, 'job': 'technician', 'marital': 'married', 'education': 'university.degree',
    'default': 'no', 'housing': 'yes', 'loan': 'no', 'contact': 'cellular', 'month': 'may', 'day_of_week': 'thu',
    'campaign': 2, 'pdays': 999, 'previous': 0, 'poutcome': 'nonexistent', 'emp_var_rate': -1.8,
    'cons_price_idx': 92.893, 'cons_conf_idx': -46.2, 'euribor3m': 1.313, 'nr_employed': 5099.1,
}


@pytest.fixture(scope='module')
def client():
    from fastapi.testclient import TestClient

    import api_prediction.main as api
    frame = make_frame(500, seed=7)
    model = build_model({'name': 'logistic_regression', 'C': 1.0, 'class_weight': 'balanced', 'max_iter': 2000,
                         'min_frequency': 10}, seed=42).fit(frame[FEATURES], (frame['y'] == 'yes').astype(int))
    manifest = {'registered_name': 'pytest-model', 'version': 1, 'mlflow_run_id': 'pytest-run',
                'model_uri': 'models:/pytest-model/1', 'tracking_uri': 'pytest://local', 'decision_threshold': 0.5,
                'qualification': 'demo-only'}
    original = (api.load_manifest, api.load_model)
    api.load_manifest = lambda: manifest
    api.load_model = lambda m: model
    os.environ['API_DB_DSN'] = DSN
    with TestClient(api.app, headers={'X-Test-Campaign': CAMPAIGN}) as test_client:
        yield test_client
    api.load_manifest, api.load_model = original


def _db(query, *params):
    import psycopg
    with psycopg.connect(DSN) as conn:
        return conn.execute(query, params).fetchall()


def test_prediction_unitaire_tracee(client):
    response = client.post('/predict', json={'client': CLIENT})
    assert response.status_code == 200
    body = response.json()
    assert body['model'] == {'registered_name': 'pytest-model', 'version': 1, 'mlflow_run_id': 'pytest-run'}
    assert 0 <= body['prediction']['probability'] <= 1
    rows = _db('SELECT http_status, item_count FROM tracing.api_requests WHERE request_id = %s', body['request_id'])
    assert rows == [(200, 1)]
    assert len(_db('SELECT 1 FROM tracing.predictions WHERE request_id = %s', body['request_id'])) == 1


def test_lot_coherent_avec_unitaire(client):
    clients = [{**CLIENT, 'client_ref': f'c{i}', 'age': 25 + 10 * i} for i in range(5)]
    batch = client.post('/predict/batch', json={'clients': clients}).json()
    units = [client.post('/predict', json={'client': c}).json()['prediction']['probability'] for c in clients]
    assert [p['item_index'] for p in batch['predictions']] == list(range(5))
    assert [p['probability'] for p in batch['predictions']] == pytest.approx(units, abs=1e-12)
    assert len(_db('SELECT 1 FROM tracing.predictions WHERE request_id = %s', batch['request_id'])) == 5


@pytest.mark.parametrize('payload', [
    {'clients': []},
    {'clients': [CLIENT] * 101},
    {'clients': [CLIENT, {**CLIENT, 'age': -4}]},
    {'clients': [CLIENT, {**CLIENT, 'campaign': 'inconnu'}]},
])
def test_lot_invalide_tout_ou_rien(client, payload):
    response = client.post('/predict/batch', json=payload)
    assert response.status_code == 422
    request_id = response.json()['request_id']
    assert _db('SELECT http_status, error_code FROM tracing.api_requests WHERE request_id = %s', request_id) == \
        [(422, 'invalid_input')]
    assert _db('SELECT 1 FROM tracing.predictions WHERE request_id = %s', request_id) == []


def test_categorie_inconnue_et_age_manquant_acceptes(client):
    response = client.post('/predict', json={'client': {**CLIENT, 'job': 'astronaute', 'age': None}})
    assert response.status_code == 200


def test_cycle_de_vie_des_feedbacks(client):
    prediction_id = client.post('/predict', json={'client': CLIENT}).json()['prediction']['prediction_id']
    feedback = {'feedback_id': str(uuid.uuid4()), 'prediction_id': prediction_id, 'useful': True,
                'observed_label': 'yes', 'observed_at': '2026-11-07T12:00:00Z', 'label_source': 'loadtest'}
    assert client.post('/feedback', json=feedback).status_code == 201
    assert client.post('/feedback', json=feedback).status_code == 200            # rejeu identique
    assert client.post('/feedback', json={**feedback, 'useful': False}).status_code == 409
    other = {**feedback, 'feedback_id': str(uuid.uuid4())}
    assert client.post('/feedback', json=other).status_code == 409               # 2e avis sur la même prédiction
    assert len(_db('SELECT 1 FROM tracing.feedbacks WHERE prediction_id = %s', prediction_id)) == 1
    unknown = {**feedback, 'feedback_id': str(uuid.uuid4()), 'prediction_id': str(uuid.uuid4())}
    assert client.post('/feedback', json=unknown).status_code == 404
    incomplete = {'feedback_id': str(uuid.uuid4()), 'prediction_id': prediction_id, 'observed_label': 'no'}
    assert client.post('/feedback', json=incomplete).status_code == 422


def test_panne_de_stockage_aucun_faux_succes(client, monkeypatch):
    service = client.app.state.service

    class PoolEnPanne:
        def connection(self, timeout=None):
            raise RuntimeError('PostgreSQL arrêté')

    monkeypatch.setattr(service, 'pool', PoolEnPanne())
    response = client.post('/predict', json={'client': CLIENT})
    assert response.status_code == 503
    assert response.json()['error_code'] == 'storage_unavailable'
    assert client.get('/health/ready').status_code == 503
    monkeypatch.undo()
    assert _db('SELECT 1 FROM tracing.api_requests WHERE request_id = %s', response.json()['request_id']) == []


def test_sante(client):
    assert client.get('/health/live').json() == {'status': 'alive'}
    assert client.get('/health/ready').json()['status'] == 'ready'
