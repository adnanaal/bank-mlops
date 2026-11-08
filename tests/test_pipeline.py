"""Comportement de la chaîne sans serveur MLflow : blocage avant prétraitement, split, modèle rechargeable."""
import pickle

import numpy as np
import pandas as pd
import pytest
from helpers import make_frame, write_project

from src import ledger
from src.contract import FEATURES
from src.data_validation import ValidationBlockedError
from src.features import add_derived_features
from src.ingestion import load_data
from src.preprocessing import preprocess_data
from src.training import build_model, train_models


def test_lot_invalide_rejete_avant_pretraitement(tmp_path, fake_tracking, monkeypatch):
    config_path = write_project(tmp_path)
    bad = pd.read_csv(tmp_path / 'data' / 'landing' / '2009-05.csv')
    bad.loc[0, 'age'] = -3
    bad.to_csv(tmp_path / 'data' / 'landing' / '2009-05.csv', index=False)
    called = []
    import src.preprocessing as preprocessing
    monkeypatch.setattr(preprocessing, 'preprocess_data', lambda data: called.append(data))
    with pytest.raises(ValidationBlockedError):
        load_data(config_path, batch='next')
    assert called == []
    entries = ledger.load(tmp_path / 'data' / 'state' / 'ledger.json')['batches']
    assert [e['status'] for e in entries.values()] == ['rejected']
    # Un lot rejeté inchangé n'est plus proposé : pas de boucle de rejeu.
    assert ledger.pending_batches(tmp_path / 'data' / 'landing', tmp_path / 'data' / 'state' / 'ledger.json') == []
    assert not (tmp_path / 'runs').exists()


def test_lot_corrige_retraitable(tmp_path, fake_tracking):
    config_path = write_project(tmp_path)
    landing = tmp_path / 'data' / 'landing' / '2009-05.csv'
    good = landing.read_bytes()
    bad = pd.read_csv(landing)
    bad.loc[0, 'age'] = -3
    bad.to_csv(landing, index=False)
    with pytest.raises(ValidationBlockedError):
        load_data(config_path, batch='next')
    landing.write_bytes(good)  # version corrigée : nouvelle empreinte, donc traçable et retraitable
    loaded = load_data(config_path, batch='next')
    assert loaded.batch['rows'] == 150
    assert len(loaded.snapshot) == 750


def test_split_sans_fuite_et_qualification_isolee(tmp_path, fake_tracking):
    loaded = load_data(write_project(tmp_path), batch='next')
    prepared = preprocess_data(loaded)
    assert set(prepared.ids_train).isdisjoint(prepared.ids_test)
    assert set(loaded.qualification['client_id']).isdisjoint(loaded.snapshot['client_id'])
    assert 'duration' not in prepared.X_train.columns


def test_pdays_999_devient_indicateur():
    frame = make_frame(5)
    frame['pdays'] = [999, 3, 999, 0, 10]
    derived = add_derived_features(frame)
    assert derived['never_contacted'].tolist() == [1.0, 0.0, 1.0, 0.0, 0.0]
    assert derived['pdays'].isna().tolist() == [True, False, True, False, False]


def test_categorie_inconnue_et_valeur_manquante_a_l_inference(tmp_path, fake_tracking):
    loaded = load_data(write_project(tmp_path), batch='next')
    trained = train_models(preprocess_data(loaded))
    sample = loaded.qualification[FEATURES].head(3).copy()
    sample.loc[sample.index[0], 'job'] = 'astronaute'
    sample.loc[sample.index[1], 'age'] = np.nan
    proba = trained.model.predict_proba(sample)[:, 1]
    assert proba.shape == (3,) and np.all((proba >= 0) & (proba <= 1))


def test_predictions_identiques_apres_serialisation():
    frame = make_frame(400)
    model = build_model({'name': 'logistic_regression', 'C': 1.0, 'class_weight': 'balanced',
                         'max_iter': 2000, 'min_frequency': 10}, seed=42)
    model.fit(frame[FEATURES], (frame['y'] == 'yes').astype(int))
    reloaded = pickle.loads(pickle.dumps(model))
    np.testing.assert_allclose(model.predict_proba(frame[FEATURES]), reloaded.predict_proba(frame[FEATURES]),
                               atol=1e-12)


def test_dossier_de_run_existant_refuse(tmp_path, fake_tracking, monkeypatch):
    from src.contract import RunDirectoryExistsError
    config_path = write_project(tmp_path)
    import src.ingestion as ingestion
    monkeypatch.setattr(ingestion, 'datetime', type('D', (), {'now': staticmethod(
        lambda tz=None: pd.Timestamp('2026-01-01T00:00:00Z').to_pydatetime())}))
    (tmp_path / 'runs' / '20260101T000000_2009-05').mkdir(parents=True)
    with pytest.raises(RunDirectoryExistsError):
        load_data(config_path, batch='next')
