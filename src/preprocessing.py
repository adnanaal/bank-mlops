"""Étape 2 — prétraitement : features, cible binaire, split stratifié et préprocesseur (ajusté plus tard sur train).

Les transformations apprises (imputation, encodage) sont dans le Pipeline et ne sont ajustées que sur train ;
la transformation déterministe (pdays = 999) est la même fonction à l'entraînement et à l'inférence.
"""
import logging
from dataclasses import dataclass

import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import FunctionTransformer, OneHotEncoder, StandardScaler

from src import tracking
from src.contract import CATEGORICAL_FEATURES, EXCLUDED_COLUMNS, FEATURES, ID_COLUMN, POSITIVE_LABEL, TARGET
from src.features import DERIVED_NUMERIC, add_derived_features
from src.ingestion import LoadedData

logger = logging.getLogger(__name__)
PREPROCESSING_VERSION = '1.0.0'


@dataclass
class PreparedData:
    loaded: LoadedData
    X_train: pd.DataFrame
    y_train: pd.Series
    X_test: pd.DataFrame
    y_test: pd.Series
    X_qualification: pd.DataFrame
    y_qualification: pd.Series
    ids_train: pd.Series
    ids_test: pd.Series


def binary_target(frame):
    return (frame[TARGET] == POSITIVE_LABEL).astype(int)


def build_preprocessor(min_frequency):
    numeric = Pipeline([('imputer', SimpleImputer(strategy='median', add_indicator=True)),
                        ('scaler', StandardScaler())])
    categorical = OneHotEncoder(handle_unknown='infrequent_if_exist', min_frequency=min_frequency,
                                sparse_output=False)
    columns = ColumnTransformer([('num', numeric, DERIVED_NUMERIC), ('cat', categorical, CATEGORICAL_FEATURES)])
    return Pipeline([('derive', FunctionTransformer(add_derived_features, validate=False)), ('columns', columns)])


def preprocess_data(data):
    config = data.config
    snapshot = data.snapshot
    X, y, ids = snapshot[FEATURES], binary_target(snapshot), snapshot[ID_COLUMN]
    X_train, X_test, y_train, y_test, ids_train, ids_test = train_test_split(
        X, y, ids, test_size=float(config['test_size']), random_state=int(config['seed']), stratify=y)
    qualification = data.qualification
    prepared = PreparedData(
        loaded=data, X_train=X_train, y_train=y_train, X_test=X_test, y_test=y_test,
        X_qualification=qualification[FEATURES], y_qualification=binary_target(qualification),
        ids_train=ids_train, ids_test=ids_test)

    run_id = data.mlflow_run_id
    split = pd.concat([pd.DataFrame({ID_COLUMN: ids_train, 'set': 'train'}),
                       pd.DataFrame({ID_COLUMN: ids_test, 'set': 'test'})]).sort_values(ID_COLUMN)
    split_path = data.snapshot_path.with_suffix('.split.csv')
    split.to_csv(split_path, index=False, lineterminator='\n')
    tracking.log_artifact(run_id, split_path, 'data')
    tracking.log_params(run_id, {
        'prep.version': PREPROCESSING_VERSION, 'prep.features': ','.join(FEATURES),
        'prep.excluded_columns': ','.join(EXCLUDED_COLUMNS) + ' (fuite : connue après l\'appel)',
        'prep.numeric': 'pdays=999 -> indicateur never_contacted + NaN ; médiane + indicateur ; standardisation',
        'prep.categorical': f"one-hot, min_frequency={config['model']['min_frequency']}, infrequent_if_exist",
        'split.strategy': 'stratifié sur y', 'split.test_size': config['test_size'], 'split.seed': config['seed'],
        'split.n_train': len(X_train), 'split.n_test': len(X_test),
        'split.n_qualification': len(prepared.X_qualification),
    })
    tracking.log_metrics(run_id, {
        'data.positive_rate_train': y_train.mean(), 'data.positive_rate_test': y_test.mean(),
        'data.positive_rate_qualification': prepared.y_qualification.mean(),
    })
    _log_inputs(run_id, prepared)
    tracking.update_open_run(data.execution_id, stage='training')
    logger.info('Split : %s train / %s test / %s qualification', len(X_train), len(X_test),
                len(prepared.X_qualification))
    return prepared


def _log_inputs(run_id, prepared):
    """Métadonnées de datasets MLflow (le snapshot complet est conservé à part comme artefact)."""
    import mlflow
    tracking.configure()
    loaded = prepared.loaded
    with mlflow.start_run(run_id=run_id):
        mlflow.log_input(mlflow.data.from_pandas(
            loaded.snapshot, source=str(loaded.snapshot_path), targets=TARGET,
            name=f'snapshot-{loaded.snapshot_sha256[:12]}'), context='training')
        mlflow.log_input(mlflow.data.from_pandas(
            loaded.qualification, source='data/prepared/qualification.csv', targets=TARGET,
            name=f'qualification-{loaded.qualification_sha256[:12]}'), context='validation')
