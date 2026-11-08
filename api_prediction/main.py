"""API FastAPI d'inférence (port 8001).

Lancement : uv run --locked uvicorn api_prediction.main:app --host 127.0.0.1 --port 8001 --workers 1

Traçabilité : 1 ligne api_requests par tentative sur /predict, /predict/batch, /feedback ; N lignes predictions
pour une requête valide de N clients, écrites dans la même transaction. Succès HTTP seulement après commit.
"""
import os

os.environ.setdefault('OMP_NUM_THREADS', '1')        # parallélisme interne fixé : 1 thread numérique par worker
os.environ.setdefault('OPENBLAS_NUM_THREADS', '1')
os.environ.setdefault('MKL_NUM_THREADS', '1')

import json  # noqa: E402
import logging  # noqa: E402
import time  # noqa: E402
import uuid  # noqa: E402
from contextlib import asynccontextmanager  # noqa: E402
from datetime import datetime, timezone  # noqa: E402

import pandas as pd  # noqa: E402
from fastapi import FastAPI, Request  # noqa: E402
from fastapi.exceptions import RequestValidationError  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402
from psycopg.types.json import Jsonb  # noqa: E402

from api_prediction.schemas import (  # noqa: E402
    BatchRequest,
    BatchResponse,
    ErrorResponse,
    FeedbackRequest,
    FeedbackResponse,
    PredictRequest,
    PredictResponse,
)
from api_prediction.service import (  # noqa: E402
    Service,
    load_manifest,
    load_model,
    log_local_failure,
    open_pool,
)
from src.contract import FEATURES  # noqa: E402

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')
logger = logging.getLogger('api_prediction')
TRACED = {'/predict', '/predict/batch', '/feedback'}


class ApiError(Exception):
    def __init__(self, status, code, message):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


@asynccontextmanager
async def lifespan(app):
    manifest = load_manifest()                 # échec → le worker ne démarre pas (jamais « prêt » sans modèle)
    model = load_model(manifest)
    pool = open_pool()
    app.state.service = Service(manifest=manifest, model=model, pool=pool)
    logger.info('Worker %s prêt à servir %s v%s', app.state.service.instance_id, manifest['registered_name'],
                manifest['version'])
    yield
    pool.close()


app = FastAPI(title='Bank Marketing — API de prédiction', version='1.0.0', lifespan=lifespan,
              description="Probabilité de souscription d'un dépôt à terme (UCI Bank Marketing). "
                          'Chaque réponse identifie la version exacte du modèle MLflow utilisée.',
              responses={422: {'model': ErrorResponse}, 503: {'model': ErrorResponse}, 500: {'model': ErrorResponse}})


@app.middleware('http')
async def request_context(request: Request, call_next):
    request.state.started = time.perf_counter()
    request.state.started_at = datetime.now(timezone.utc)
    request.state.request_id = uuid.uuid4()
    campaign = request.headers.get('X-Test-Campaign', 'manual')[:128] or 'manual'
    request.state.campaign = campaign
    response = await call_next(request)
    response.headers['X-Request-Id'] = str(request.state.request_id)
    return response


def _elapsed_ms(request):
    return (time.perf_counter() - request.state.started) * 1000


def _request_row(request, status, item_count, processing_ms, inference_ms=None, error_code=None):
    service = request.app.state.service
    return (request.state.request_id, request.state.started_at, request.url.path, status, item_count,
            processing_ms, inference_ms, service.worker_pid, service.instance_id, service.revision,
            request.state.campaign, error_code)


INSERT_REQUEST = """INSERT INTO tracing.api_requests (request_id, started_at, endpoint, http_status, item_count,
    processing_duration_ms, inference_duration_ms, worker_pid, instance_id, api_revision, campaign_id, error_code)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"""


def _trace_error(request, status, code, item_count=None):
    """Trace d'une requête en erreur, dans sa propre transaction ; journal local si la base est absente."""
    if request.url.path not in TRACED or not hasattr(request.app.state, 'service'):
        return
    processing = _elapsed_ms(request)
    try:
        with request.app.state.service.pool.connection(timeout=2) as conn:
            conn.execute(INSERT_REQUEST, _request_row(request, status, item_count, processing, None, code))
    except Exception as exc:  # noqa: BLE001
        log_local_failure({'request_id': request.state.request_id, 'endpoint': request.url.path,
                           'status': status, 'error_code': code, 'db_error': str(exc)[:300]})


def _error(request, status, code, message, item_count=None):
    _trace_error(request, status, code, item_count)
    body = ErrorResponse(request_id=request.state.request_id, error_code=code, message=message)
    return JSONResponse(status_code=status, content=json.loads(body.model_dump_json()))


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError):
    details = [{'loc': list(e.get('loc', [])), 'msg': e.get('msg')} for e in exc.errors()][:10]
    return _error(request, 422, 'invalid_input', json.dumps(details, ensure_ascii=False))


@app.exception_handler(ApiError)
async def api_error_handler(request: Request, exc: ApiError):
    return _error(request, exc.status, exc.code, exc.message)


@app.exception_handler(Exception)
async def unexpected_handler(request: Request, exc: Exception):
    logger.exception('Erreur inattendue (request_id=%s)', getattr(request.state, 'request_id', None))
    return _error(request, 500, 'internal_error', 'Erreur interne ; voir le journal serveur avec request_id')


def _predict(request, clients):
    service = request.app.state.service
    frame = pd.DataFrame([c.features() for c in clients])[FEATURES]
    started = time.perf_counter()
    try:
        proba = service.model.predict_proba(frame)[:, 1]
    except Exception as exc:  # noqa: BLE001
        logger.exception('Inférence impossible')
        raise ApiError(503, 'model_unavailable', 'Le modèle ne peut pas produire de prédiction') from exc
    inference_ms = (time.perf_counter() - started) * 1000
    predictions = []
    for index, (client, probability) in enumerate(zip(clients, proba, strict=False)):
        predictions.append({
            'prediction_id': uuid.uuid4(), 'item_index': index, 'client_ref': client.client_ref,
            'probability': float(probability), 'label': 'yes' if probability >= service.threshold else 'no',
            'decision_threshold': service.threshold, 'features': client.features(),
        })
    return predictions, inference_ms


def _store_predictions(request, predictions, inference_ms):
    """Une transaction : la requête + toutes ses prédictions, ou rien (pas de prédiction partielle)."""
    service = request.app.state.service
    processing_ms = _elapsed_ms(request)
    if len({p['item_index'] for p in predictions}) != len(predictions):
        raise RuntimeError('Indices de lot incohérents')
    try:
        with service.pool.connection(timeout=3) as conn:
            with conn.transaction():
                model_id = service.ensure_model_id(conn)
                conn.execute(INSERT_REQUEST, _request_row(request, 200, len(predictions), processing_ms,
                                                          inference_ms))
                with conn.cursor() as cur:
                    cur.executemany(
                        """INSERT INTO tracing.predictions (prediction_id, request_id, item_index, client_ref, model_id,
                               input_features, predicted_proba, predicted_label, decision_threshold)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                        [(p['prediction_id'], request.state.request_id, p['item_index'], p['client_ref'], model_id,
                          Jsonb(p['features']), p['probability'], p['label'], p['decision_threshold'])
                         for p in predictions])
    except Exception as exc:  # noqa: BLE001 - rollback déjà effectué par le contexte de transaction
        logger.error('Transaction de traçabilité annulée : %s', exc)
        raise ApiError(503, 'storage_unavailable', 'Traçabilité indisponible : aucune prédiction servie') from exc


def _public(p):
    return {k: v for k, v in p.items() if k != 'features'}


@app.post('/predict', response_model=PredictResponse, tags=['prédiction'])
def predict(body: PredictRequest, request: Request):
    predictions, inference_ms = _predict(request, [body.client])
    _store_predictions(request, predictions, inference_ms)
    return {'request_id': request.state.request_id, 'model': request.app.state.service.identity(),
            'prediction': _public(predictions[0])}


@app.post('/predict/batch', response_model=BatchResponse, tags=['prédiction'])
def predict_batch(body: BatchRequest, request: Request):
    predictions, inference_ms = _predict(request, body.clients)
    _store_predictions(request, predictions, inference_ms)
    return {'request_id': request.state.request_id, 'model': request.app.state.service.identity(),
            'predictions': [_public(p) for p in predictions]}


@app.post('/feedback', response_model=FeedbackResponse, status_code=201, tags=['feedback'],
          responses={200: {'model': FeedbackResponse}, 404: {'model': ErrorResponse}, 409: {'model': ErrorResponse}})
def feedback(body: FeedbackRequest, request: Request):
    service = request.app.state.service
    processing_ms = _elapsed_ms(request)
    content = (body.useful, body.observed_label, body.observed_at, body.label_source)
    try:
        with service.pool.connection(timeout=3) as conn:
            with conn.transaction():
                if conn.execute('SELECT 1 FROM tracing.predictions WHERE prediction_id = %s',
                                (body.prediction_id,)).fetchone() is None:
                    raise ApiError(404, 'prediction_not_found', 'Prédiction inconnue')
                conn.execute(INSERT_REQUEST, _request_row(request, 201, None, processing_ms))
                created = conn.execute(
                    """INSERT INTO tracing.feedbacks (feedback_id, request_id, prediction_id, useful, observed_label,
                           observed_at, label_source) VALUES (%s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT DO NOTHING RETURNING feedback_id""",
                    (body.feedback_id, request.state.request_id, body.prediction_id, *content)).fetchone()
                if created is None:
                    existing = conn.execute(
                        """SELECT feedback_id, prediction_id, useful, observed_label, observed_at, label_source
                           FROM tracing.feedbacks WHERE feedback_id = %s OR prediction_id = %s""",
                        (body.feedback_id, body.prediction_id)).fetchall()
                    same = any(row[0] == body.feedback_id and row[1] == body.prediction_id
                               and tuple(row[2:]) == content for row in existing)
                    if not same:
                        raise ApiError(409, 'feedback_conflict',
                                       'Identifiant réutilisé avec un autre contenu ou prédiction déjà évaluée')
                    # Rejeu identique : on annule cette transaction (ligne 201 provisoire) puis on trace
                    # la tentative avec son vrai statut 200, sans nouveau feedback.
                    raise _Replay()
    except _Replay:
        _trace_replay(request, processing_ms)
        return JSONResponse(status_code=200, content={'request_id': str(request.state.request_id),
                                                      'feedback_id': str(body.feedback_id),
                                                      'status': 'already_recorded'})
    except ApiError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ApiError(503, 'storage_unavailable', 'Stockage des feedbacks indisponible') from exc
    return {'request_id': request.state.request_id, 'feedback_id': body.feedback_id, 'status': 'created'}


class _Replay(Exception):
    """Rejeu identique d'un feedback : la transaction est annulée puis seule la tentative est tracée."""


def _trace_replay(request, processing_ms):
    try:
        with request.app.state.service.pool.connection(timeout=3) as conn:
            conn.execute(INSERT_REQUEST, _request_row(request, 200, None, processing_ms))
    except Exception as exc:  # noqa: BLE001
        log_local_failure({'request_id': request.state.request_id, 'endpoint': '/feedback', 'status': 200,
                           'db_error': str(exc)[:300]})


@app.get('/health/live', tags=['santé'])
def live():
    return {'status': 'alive'}


@app.get('/health/ready', tags=['santé'])
def ready(request: Request):
    service = request.app.state.service
    if service.model is None or not service.db_ready():
        return JSONResponse(status_code=503, content={'status': 'not_ready', 'model_loaded': service.model is not None,
                                                      'database': False})
    return {'status': 'ready', 'model': service.identity(), 'instance_id': str(service.instance_id)}


@app.get('/model-info', tags=['santé'])
def model_info(request: Request):
    service = request.app.state.service
    return {**service.manifest, 'instance_id': str(service.instance_id), 'worker_pid': service.worker_pid,
            'api_revision': service.revision}
