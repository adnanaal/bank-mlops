"""La barrière GX accepte un lot conforme et bloque chaque type de non-conformité, avec un rapport."""
import json

import pandas as pd
import pytest
from helpers import QUALITY, make_frame

from src.data_validation import ValidationBlockedError, batch_quality_table, to_csv_bytes, validate


def _validate(batch, tmp_path, known=(), qualification=()):
    audit = batch_quality_table(batch, QUALITY, set(known), set(qualification))
    return validate([('bank_lot', 'lot', batch), ('bank_lot_qualite', 'audit', audit)], stage='lot',
                    source='test', snapshot_bytes=to_csv_bytes(batch), diagnostics_dir=tmp_path, quality=QUALITY)


def test_lot_conforme_accepte(tmp_path):
    report_path, report = _validate(make_frame(150), tmp_path)
    assert report['success'] is True
    assert report_path.name.endswith('_ok.json')


@pytest.mark.parametrize('defaut, attendu', [
    ('age_negatif', 'age dans'),
    ('texte_numerique', 'campaign numérique'),
    ('colonne_absente', 'Colonnes du contrat'),
    ('client_id_duplique', 'client_id unique'),
    ('categorie_inconnue', 'job dans le domaine'),
    ('trop_de_unknown', 'Part de "unknown" pour job'),
    ('cible_invalide', 'y vaut yes ou no'),
    ('lot_trop_petit', 'Au moins 100 lignes'),
])
def test_non_conformites_bloquantes(tmp_path, defaut, attendu):
    batch = make_frame(150)
    if defaut == 'age_negatif':
        batch.loc[0, 'age'] = -3
    elif defaut == 'texte_numerique':
        batch['campaign'] = batch['campaign'].astype(object)
        batch.loc[0, 'campaign'] = 'inconnu'
    elif defaut == 'colonne_absente':
        batch = batch.drop(columns=['euribor3m'])
    elif defaut == 'client_id_duplique':
        batch.loc[1, 'client_id'] = batch.loc[0, 'client_id']
    elif defaut == 'categorie_inconnue':
        batch.loc[0, 'job'] = 'astronaute'
    elif defaut == 'trop_de_unknown':
        batch.loc[batch.index[:60], 'job'] = 'unknown'
    elif defaut == 'cible_invalide':
        batch.loc[0, 'y'] = 'peut-être'
    elif defaut == 'lot_trop_petit':
        batch = batch.head(40)
    with pytest.raises(ValidationBlockedError) as error:
        _validate(batch, tmp_path)
    assert attendu in str(error.value)
    report = json.loads(error.value.report_path.read_text(encoding='utf-8'))
    assert report['success'] is False
    assert (tmp_path / 'rejected').exists()


def test_fuite_vers_corpus_de_qualification_bloquee(tmp_path):
    batch = make_frame(150, start_id=500)
    with pytest.raises(ValidationBlockedError, match='corpus de qualification'):
        _validate(batch, tmp_path, qualification={500})


def test_client_deja_connu_bloque(tmp_path):
    batch = make_frame(150, start_id=500)
    with pytest.raises(ValidationBlockedError, match='déjà présent'):
        _validate(batch, tmp_path, known={501})


def test_erreur_gx_bloquante(tmp_path):
    class RunnerEnPanne:
        def run(self, suite_name, frame):
            raise RuntimeError('GX indisponible')

    batch = make_frame(150)
    with pytest.raises(ValidationBlockedError, match='GX indisponible'):
        validate([('bank_lot', 'lot', batch)], stage='lot', source='test', snapshot_bytes=b'x',
                 diagnostics_dir=tmp_path, quality=QUALITY, runner=RunnerEnPanne())


def test_resultat_absent_bloquant(tmp_path):
    class RunnerSansResultat:
        def run(self, suite_name, frame):
            return None

    with pytest.raises(ValidationBlockedError, match='absent'):
        validate([('bank_lot', 'lot', pd.DataFrame())], stage='lot', source='test', snapshot_bytes=b'x',
                 diagnostics_dir=tmp_path, quality=QUALITY, runner=RunnerSansResultat())
