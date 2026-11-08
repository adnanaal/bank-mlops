"""Étape 3 — entraînement : pipeline complet (prétraitement + régression logistique) et baseline."""
import logging
import time
from dataclasses import dataclass

from sklearn.dummy import DummyClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline

from src import tracking
from src.preprocessing import PreparedData, build_preprocessor

logger = logging.getLogger(__name__)


@dataclass
class TrainedModels:
    prepared: PreparedData
    model: Pipeline
    baseline: DummyClassifier
    training_seconds: float


def build_model(model_config, seed):
    if model_config['name'] != 'logistic_regression':
        raise ValueError(f"Modèle non supporté : {model_config['name']}")
    classifier = LogisticRegression(C=float(model_config['C']), class_weight=model_config['class_weight'],
                                    max_iter=int(model_config['max_iter']), random_state=seed)
    return Pipeline([('preprocess', build_preprocessor(int(model_config['min_frequency']))),
                     ('classifier', classifier)])


def train_models(prepared):
    loaded = prepared.loaded
    config = loaded.config
    seed = int(config['seed'])
    model = build_model(config['model'], seed)
    started = time.perf_counter()
    model.fit(prepared.X_train, prepared.y_train)
    elapsed = time.perf_counter() - started
    baseline = DummyClassifier(strategy='prior').fit(prepared.X_train, prepared.y_train)
    tracking.log_params(loaded.mlflow_run_id, {
        **{f'model.{key}': value for key, value in config['model'].items()},
        'model.seed': seed, 'model.decision_threshold': config['decision_threshold'],
        'baseline.strategy': 'prior (prévalence du train)',
        'model.n_features_out': len(model.named_steps['preprocess'].named_steps['columns'].get_feature_names_out()),
    })
    tracking.log_metrics(loaded.mlflow_run_id, {'timing.training_seconds': elapsed})
    tracking.update_open_run(loaded.execution_id, stage='evaluation')
    logger.info('Modèle entraîné en %.2f s', elapsed)
    return TrainedModels(prepared=prepared, model=model, baseline=baseline, training_seconds=elapsed)
