"""Générer le dashboard Grafana (JSON provisionné) : requêtes SQL versionnées ici.

Règles : débit, erreurs et durées partent de api_requests (une ligne par tentative HTTP, jamais multipliée par
une jointure) ; modèles et feedbacks joignent predictions en respectant leur granularité ; percentiles calculés
sur les observations, jamais en moyennant des percentiles. Labels artificiels exclus de la qualité observée.

Usage : uv run --locked python grafana/build_dashboard.py
"""
import json
from pathlib import Path

DS = {'type': 'grafana-postgresql-datasource', 'uid': 'bank-api-postgres'}
FILTER = "$__timeFilter(r.started_at) AND r.campaign_id IN ($campaign) AND r.endpoint IN ($endpoint)"
ARTIFICIAL = "('loadtest', 'synthetic-course')"


def target(sql, fmt='time_series', ref='A'):
    return {'refId': ref, 'datasource': DS, 'rawQuery': True, 'editorMode': 'code', 'format': fmt, 'rawSql': sql}


def panel(pid, title, kind, grid, targets, unit=None, description='', options=None, overrides=None, bars=False):
    field_config = {'defaults': {'noValue': '—' if kind == 'table' else 'pas de données'},
                    'overrides': overrides or []}
    if unit:
        field_config['defaults']['unit'] = unit
    options = dict(options or {})
    if kind == 'stat':
        # Couleur neutre (pas de seuil arbitraire) ; champs texte acceptés (version servie).
        field_config['defaults']['color'] = {'mode': 'fixed', 'fixedColor': 'text'}
        options.setdefault('colorMode', 'none')
        options.setdefault('reduceOptions', {'values': False, 'calcs': ['lastNotNull'], 'fields': '/.*/'})
    if kind == 'timeseries':
        # Pas de ligne tracée à travers les périodes sans activité (> 10 min).
        field_config['defaults']['custom'] = {
            'drawStyle': 'bars' if bars else 'line', 'insertNulls': 600000, 'spanNulls': False,
            'showPoints': 'auto', 'lineWidth': 1, 'fillOpacity': 80 if bars else 0}
    return {'id': pid, 'title': title, 'type': kind, 'datasource': DS, 'gridPos': grid, 'description': description,
            'targets': targets, 'fieldConfig': field_config, 'options': options}


def row(pid, title, y):
    return {'id': pid, 'type': 'row', 'title': title, 'collapsed': False, 'gridPos': {'h': 1, 'w': 24, 'x': 0, 'y': y},
            'panels': []}


def g(x, y, w, h):
    return {'x': x, 'y': y, 'w': w, 'h': h}


PANELS = [
    row(100, 'Usage du service', 0),
    panel(1, 'Requêtes (tentatives)', 'stat', g(0, 1, 4, 4), [target(
        f"SELECT count(*) AS requetes FROM tracing.api_requests r WHERE {FILTER}", 'table')], 'short'),
    panel(2, "Taux d'erreur", 'stat', g(4, 1, 4, 4), [target(
        f"SELECT CASE WHEN count(*) = 0 THEN NULL ELSE 100.0 * avg((r.http_status >= 400)::int) END AS erreurs "
        f"FROM tracing.api_requests r WHERE {FILTER}", 'table')], 'percent'),
    panel(3, 'Clients scorés (succès)', 'stat', g(8, 1, 4, 4), [target(
        f"SELECT coalesce(sum(r.item_count), 0) AS clients FROM tracing.api_requests r WHERE {FILTER} "
        "AND r.http_status = 200 AND r.endpoint <> '/feedback'", 'table')], 'short'),
    panel(4, 'Version servie (dernière prédiction)', 'stat', g(12, 1, 6, 4), [target(
        "SELECT mv.registered_name || ' v' || mv.version || ' (' || coalesce(mv.qualification, '?') || ')' AS version "
        "FROM tracing.predictions p JOIN tracing.model_versions mv USING (model_id) "
        "JOIN tracing.api_requests r USING (request_id) "
        "WHERE $__timeFilter(p.created_at) AND r.campaign_id IN ($campaign) ORDER BY p.created_at DESC LIMIT 1",
        'table')],
        options={'textMode': 'value'}),
    panel(5, 'p95 traitement /predict', 'stat', g(18, 1, 6, 4), [target(
        "SELECT percentile_cont(0.95) WITHIN GROUP (ORDER BY r.processing_duration_ms) AS p95 "
        "FROM tracing.api_requests r WHERE $__timeFilter(r.started_at) AND r.campaign_id IN ($campaign) "
        "AND r.endpoint = '/predict' AND r.http_status = 200", 'table')], 'ms'),
    panel(6, 'Requêtes par seconde (succès et tentatives)', 'timeseries', g(0, 5, 12, 8), [target(
        "SELECT $__timeGroupAlias(r.started_at, $__interval), r.endpoint || CASE WHEN r.http_status < 400 THEN ' succès' "
        "ELSE ' erreur' END AS metric, count(*) / ($__interval_ms / 1000.0) AS value "
        f"FROM tracing.api_requests r WHERE {FILTER} GROUP BY 1, 2 ORDER BY 1")], 'reqps'),
    panel(7, 'Clients scorés par seconde', 'timeseries', g(12, 5, 12, 8), [target(
        "SELECT $__timeGroupAlias(r.started_at, $__interval), r.endpoint AS metric, "
        "sum(r.item_count) / ($__interval_ms / 1000.0) AS value FROM tracing.api_requests r "
        f"WHERE {FILTER} AND r.http_status = 200 AND r.endpoint <> '/feedback' GROUP BY 1, 2 ORDER BY 1")], 'short',
        'Un lot de 20 compte 20 clients : à comparer aux requêtes/s.'),
    row(101, 'Erreurs et performances', 13),
    panel(8, 'Erreurs par endpoint et code', 'timeseries', g(0, 14, 12, 8), [target(
        "SELECT $__timeGroupAlias(r.started_at, $__interval), r.endpoint || ' ' || r.http_status || ' ' || "
        "r.error_code AS metric, count(*) AS value FROM tracing.api_requests r "
        f"WHERE {FILTER} AND r.http_status >= 400 GROUP BY 1, 2 ORDER BY 1")], 'short',
        'Une panne PostgreSQL ne laisse pas de trace ici : comparer avec Locust et le journal local.',
        bars=True),
    panel(9, 'Durée de traitement p50 / p95 / p99 (ms) par endpoint', 'timeseries', g(12, 14, 12, 8), [target(
        "SELECT $__timeGroupAlias(r.started_at, $__interval), r.endpoint || ' ' || q.name AS metric, "
        "percentile_cont(q.p) WITHIN GROUP (ORDER BY r.processing_duration_ms) AS value "
        "FROM tracing.api_requests r CROSS JOIN (VALUES ('p50', 0.5), ('p95', 0.95), ('p99', 0.99)) AS q(name, p) "
        f"WHERE {FILTER} AND r.http_status < 400 GROUP BY 1, 2, q.p ORDER BY 1")], 'ms',
        "Durée serveur (validation + inférence), hors transaction de traçabilité et réseau."),
    panel(10, "Durée d'inférence p95 (ms), série distincte", 'timeseries', g(0, 22, 12, 7), [target(
        "SELECT $__timeGroupAlias(r.started_at, $__interval), r.endpoint AS metric, "
        "percentile_cont(0.95) WITHIN GROUP (ORDER BY r.inference_duration_ms) AS value "
        f"FROM tracing.api_requests r WHERE {FILTER} AND r.inference_duration_ms IS NOT NULL "
        "GROUP BY 1, 2 ORDER BY 1")], 'ms'),
    panel(11, 'Taille des lots (requêtes /predict/batch)', 'barchart', g(12, 22, 6, 7), [target(
        "SELECT r.item_count::text AS taille, count(*) AS requetes FROM tracing.api_requests r "
        f"WHERE {FILTER} AND r.endpoint = '/predict/batch' AND r.http_status = 200 GROUP BY r.item_count "
        "ORDER BY r.item_count", 'table')]),
    panel(12, 'Activité par worker (instance_id / PID)', 'table', g(18, 22, 6, 7), [target(
        "SELECT r.worker_pid AS pid, left(r.instance_id::text, 8) AS instance, count(*) AS requetes, "
        f"min(r.started_at) AS premiere, max(r.started_at) AS derniere FROM tracing.api_requests r WHERE {FILTER} "
        "GROUP BY 1, 2 ORDER BY derniere DESC", 'table')],
        description="Compteur d'activité, pas une mesure CPU."),
    row(102, 'Modèles et retours', 29),
    panel(13, 'Prédictions par version de modèle', 'timeseries', g(0, 30, 12, 7), [target(
        "SELECT $__timeGroupAlias(p.created_at, $__interval), mv.registered_name || ' v' || mv.version AS metric, "
        "count(*) AS value FROM tracing.predictions p JOIN tracing.model_versions mv USING (model_id) "
        "JOIN tracing.api_requests r USING (request_id) WHERE $__timeFilter(p.created_at) "
        "AND r.campaign_id IN ($campaign) GROUP BY 1, 2 ORDER BY 1")], 'short'),
    panel(14, 'Feedbacks : volume, avis favorables, couverture', 'table', g(12, 30, 12, 7), [target(
        "WITH cohorte AS (SELECT p.prediction_id FROM tracing.predictions p JOIN tracing.api_requests r "
        "USING (request_id) WHERE $__timeFilter(p.created_at) AND r.campaign_id IN ($campaign)) "
        "SELECT (SELECT count(*) FROM cohorte) AS predictions_cohorte, count(f.feedback_id) AS feedbacks_recus, "
        "CASE WHEN count(f.useful) = 0 THEN NULL ELSE round(100.0 * avg(f.useful::int), 1) END AS pct_utiles, "
        "CASE WHEN (SELECT count(*) FROM cohorte) = 0 THEN NULL "
        "ELSE round(100.0 * count(f.feedback_id) / (SELECT count(*) FROM cohorte), 2) END AS couverture_pct "
        "FROM cohorte c LEFT JOIN tracing.feedbacks f USING (prediction_id)", 'table')],
        description="Cohorte : prédictions de la fenêtre ; retours éventuellement reçus plus tard."),
    panel(15, 'Qualité observée (labels réels, hors loadtest/synthétiques)', 'table', g(0, 37, 12, 6), [target(
        "SELECT mv.registered_name || ' v' || mv.version AS version, count(*) AS labels_observes, "
        "CASE WHEN count(*) = 0 THEN NULL ELSE round(100.0 * avg((p.predicted_label = f.observed_label)::int), 1) "
        "END AS classement_correct_pct FROM tracing.feedbacks f JOIN tracing.predictions p USING (prediction_id) "
        "JOIN tracing.model_versions mv USING (model_id) WHERE $__timeFilter(p.created_at) "
        f"AND f.observed_label IS NOT NULL AND f.label_source NOT IN {ARTIFICIAL} GROUP BY 1", 'table')],
        description="Population auto-sélectionnée : interpréter avec l'effectif. Vide si aucun label réel."),
    panel(16, 'Labels observés de test (loadtest), pour vérifier le mécanisme', 'table', g(12, 37, 12, 6), [target(
        "SELECT f.label_source, count(*) AS labels, round(100.0 * avg((p.predicted_label = f.observed_label)::int), 1) "
        "AS accord_pct FROM tracing.feedbacks f JOIN tracing.predictions p USING (prediction_id) "
        f"WHERE $__timeFilter(p.created_at) AND f.label_source IN {ARTIFICIAL} GROUP BY 1", 'table')],
        description='Labels artificiels : jamais une mesure de qualité réelle.'),
    row(103, 'Traitements (pipeline Prefect)', 43),
    panel(17, 'Exécutions du pipeline par issue', 'timeseries', g(0, 44, 10, 8), [target(
        "SELECT $__timeGroupAlias(occurred_at, $__interval), event AS metric, count(*) AS value "
        "FROM tracing.pipeline_events WHERE $__timeFilter(occurred_at) GROUP BY 1, 2 ORDER BY 1")], 'short',
        bars=True),
    panel(18, 'Derniers traitements', 'table', g(10, 44, 14, 8), [target(
        "SELECT occurred_at AS date, event AS evenement, trigger AS declencheur, batch_id AS lot, "
        "rows_in_snapshot AS lignes, model_version AS version, qualification, selected AS champion, "
        "failed_stage AS etape_echec, left(error, 160) AS erreur, duration_s AS duree_s "
        "FROM tracing.pipeline_events WHERE $__timeFilter(occurred_at) ORDER BY occurred_at DESC LIMIT 30",
        'table')]),
]

DASHBOARD = {
    'uid': 'bank-mlops-api', 'title': 'Bank MLOps — usage, performances et traitements', 'editable': False,
    'schemaVersion': 39, 'version': 1, 'refresh': '30s', 'time': {'from': 'now-6h', 'to': 'now'},
    'tags': ['bank-mlops'], 'timezone': 'browser',
    'templating': {'list': [
        {'name': 'campaign', 'label': 'Campagne', 'type': 'query', 'datasource': DS, 'multi': True,
         'includeAll': True, 'current': {'text': 'All', 'value': '$__all'}, 'refresh': 2, 'sort': 1,
         'query': "SELECT DISTINCT campaign_id FROM tracing.api_requests WHERE $__timeFilter(started_at) "
                  "AND campaign_id NOT LIKE 'pytest-%'"},
        {'name': 'endpoint', 'label': 'Endpoint', 'type': 'custom', 'multi': True, 'includeAll': True,
         'current': {'text': 'All', 'value': '$__all'}, 'query': '/predict,/predict/batch,/feedback'},
    ]},
    'panels': PANELS,
}

if __name__ == '__main__':
    target_path = Path(__file__).resolve().parent / 'dashboards' / 'bank-api.json'
    target_path.write_text(json.dumps(DASHBOARD, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'Dashboard écrit : {target_path}')
