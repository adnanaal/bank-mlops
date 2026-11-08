-- Schéma de traçabilité (adapté du TP5 5MLDE à une classification binaire unique).
-- Réexécutable sur ce même schéma ; ce fichier n'est pas un outil de migration.
\set ON_ERROR_STOP on
\getenv api_password API_DB_PASSWORD
\getenv grafana_password GRAFANA_DB_PASSWORD

BEGIN;

DO $roles$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'api_writer') THEN
        CREATE ROLE api_writer LOGIN;
    END IF;
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'grafana_reader') THEN
        CREATE ROLE grafana_reader LOGIN;
    END IF;
END
$roles$;

ALTER ROLE api_writer PASSWORD :'api_password';
ALTER ROLE grafana_reader PASSWORD :'grafana_password';
ALTER ROLE grafana_reader SET statement_timeout = '10s';
ALTER ROLE grafana_reader SET default_transaction_read_only = on;
REVOKE ALL ON DATABASE bank_api FROM PUBLIC;
GRANT CONNECT ON DATABASE bank_api TO api_writer, grafana_reader;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
CREATE SCHEMA IF NOT EXISTS tracing;
REVOKE ALL ON SCHEMA tracing FROM PUBLIC;
GRANT USAGE ON SCHEMA tracing TO api_writer, grafana_reader;

-- Version exacte d'un modèle chargée depuis MLflow (jamais un alias mutable seul).
CREATE TABLE IF NOT EXISTS tracing.model_versions (
    model_id uuid PRIMARY KEY,
    tracking_uri text NOT NULL CHECK (length(tracking_uri) > 0),
    registered_name text NOT NULL CHECK (length(registered_name) > 0),
    version bigint NOT NULL CHECK (version > 0),
    mlflow_run_id text NOT NULL CHECK (length(mlflow_run_id) > 0),
    model_uri text NOT NULL CHECK (length(model_uri) > 0),
    qualification text,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tracking_uri, registered_name, version)
);

-- Une ligne par tentative HTTP sur les endpoints métier, y compris 4xx/5xx quand la base est disponible.
CREATE TABLE IF NOT EXISTS tracing.api_requests (
    request_id uuid PRIMARY KEY,
    started_at timestamptz NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    endpoint text NOT NULL CHECK (endpoint IN ('/predict', '/predict/batch', '/feedback')),
    http_status smallint NOT NULL CHECK (http_status BETWEEN 200 AND 599),
    item_count integer CHECK (item_count >= 0),
    processing_duration_ms double precision NOT NULL
        CHECK (processing_duration_ms >= 0 AND processing_duration_ms < 'Infinity'::double precision),
    inference_duration_ms double precision
        CHECK (inference_duration_ms >= 0 AND inference_duration_ms < 'Infinity'::double precision),
    worker_pid integer NOT NULL CHECK (worker_pid > 0),
    instance_id uuid NOT NULL,
    api_revision text NOT NULL CHECK (length(api_revision) > 0),
    campaign_id text NOT NULL DEFAULT 'manual' CHECK (length(campaign_id) BETWEEN 1 AND 128),
    error_code text,
    CHECK ((http_status < 400 AND error_code IS NULL) OR (http_status >= 400 AND error_code IS NOT NULL)),
    CHECK (http_status >= 400 OR endpoint = '/feedback' OR (item_count IS NOT NULL AND item_count BETWEEN 1 AND 100)),
    CHECK (http_status >= 400 OR endpoint <> '/predict' OR item_count = 1)
);
COMMENT ON COLUMN tracing.api_requests.processing_duration_ms IS
    'Horloge monotone : début du traitement HTTP jusqu''avant la transaction de traçabilité finale.';
COMMENT ON COLUMN tracing.api_requests.inference_duration_ms IS
    'Temps cumulé des appels predict_proba pour toute la requête ; jamais à diviser par le nombre de clients.';

-- Une ligne par client prédit : une requête valide de N clients produit N lignes.
CREATE TABLE IF NOT EXISTS tracing.predictions (
    prediction_id uuid PRIMARY KEY,
    request_id uuid NOT NULL REFERENCES tracing.api_requests(request_id),
    item_index integer NOT NULL CHECK (item_index BETWEEN 0 AND 99),
    client_ref text CHECK (client_ref IS NULL OR length(client_ref) <= 64),
    model_id uuid NOT NULL REFERENCES tracing.model_versions(model_id),
    input_features jsonb NOT NULL CHECK (jsonb_typeof(input_features) = 'object'),
    predicted_proba double precision NOT NULL CHECK (predicted_proba BETWEEN 0 AND 1),
    predicted_label text NOT NULL CHECK (predicted_label IN ('yes', 'no')),
    decision_threshold double precision NOT NULL CHECK (decision_threshold BETWEEN 0 AND 1),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (request_id, item_index)
);

-- Un feedback immuable par prédiction ; useful est subjectif, observed_label est un label observé.
CREATE TABLE IF NOT EXISTS tracing.feedbacks (
    feedback_id uuid PRIMARY KEY,
    request_id uuid NOT NULL UNIQUE REFERENCES tracing.api_requests(request_id),
    prediction_id uuid NOT NULL UNIQUE REFERENCES tracing.predictions(prediction_id),
    useful boolean,
    observed_label text CHECK (observed_label IS NULL OR observed_label IN ('yes', 'no')),
    observed_at timestamptz,
    label_source text,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (useful IS NOT NULL OR observed_label IS NOT NULL),
    CHECK ((observed_label IS NULL AND observed_at IS NULL AND label_source IS NULL)
        OR (observed_label IS NOT NULL AND observed_at IS NOT NULL AND label_source IS NOT NULL
            AND length(label_source) > 0))
);

-- Journal des traitements (flows Prefect) pour le dashboard : événements immuables.
CREATE TABLE IF NOT EXISTS tracing.pipeline_events (
    event_id uuid PRIMARY KEY,
    occurred_at timestamptz NOT NULL,
    event text NOT NULL CHECK (event IN ('started', 'completed', 'failed', 'crashed')),
    execution_id text NOT NULL,
    flow_run_id text,
    trigger text,
    batch_id text,
    rows_in_batch integer,
    rows_in_snapshot integer,
    mlflow_run_id text,
    model_version bigint,
    qualification text,
    selected boolean,
    failed_stage text,
    error text,
    duration_s double precision
);

CREATE INDEX IF NOT EXISTS api_requests_started_idx ON tracing.api_requests(started_at);
CREATE INDEX IF NOT EXISTS api_requests_campaign_time_idx ON tracing.api_requests(campaign_id, started_at);
CREATE INDEX IF NOT EXISTS predictions_model_time_idx ON tracing.predictions(model_id, created_at);
CREATE INDEX IF NOT EXISTS feedbacks_created_idx ON tracing.feedbacks(created_at);
CREATE INDEX IF NOT EXISTS pipeline_events_time_idx ON tracing.pipeline_events(occurred_at);

-- Aucun compte applicatif ne peut modifier ou effacer l'historique.
GRANT SELECT, INSERT ON tracing.model_versions, tracing.api_requests, tracing.predictions,
    tracing.feedbacks, tracing.pipeline_events TO api_writer;
GRANT SELECT ON ALL TABLES IN SCHEMA tracing TO grafana_reader;

COMMIT;
