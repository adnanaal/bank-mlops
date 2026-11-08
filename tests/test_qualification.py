"""Politique de qualification : résultats contrôlés (pas des métriques MLflow réelles)."""
import json
from pathlib import Path

import numpy as np
import pytest

from src.evaluation import classification_metrics
from src.qualification import decide, should_replace_champion

POLICY = json.loads((Path(__file__).resolve().parents[1] / 'configs' / 'qualification_policy.json')
                    .read_text(encoding='utf-8'))
GOOD = {'qualification.roc_auc': 0.80, 'qualification.roc_auc_gain_vs_baseline': 0.30,
        'qualification.pr_auc_lift': 3.5, 'qualification.recall': 0.62}
TECH_OK = {'metrics_finite': True, 'reload_identical': True, 'qualification_corpus_matches': True,
           'provenance_real': True}


def test_candidat_accepte():
    assert decide(GOOD, TECH_OK, POLICY)['status'] == 'production-ready'


def test_meilleur_que_baseline_mais_insuffisant():
    decision = decide({**GOOD, 'qualification.roc_auc': 0.66}, TECH_OK, POLICY)
    assert decision['status'] == 'rejected'
    assert 'auc_min' in decision['reason']


def test_bon_auc_mais_rappel_degrade():
    decision = decide({**GOOD, 'qualification.recall': 0.2}, TECH_OK, POLICY)
    assert decision['status'] == 'rejected' and 'recall_min' in decision['reason']


@pytest.mark.parametrize('valeur', [None, float('nan'), float('inf')])
def test_metrique_absente_ou_non_finie(valeur):
    metrics = dict(GOOD)
    metrics['qualification.pr_auc_lift'] = valeur
    if valeur is None:
        metrics.pop('qualification.pr_auc_lift')
    assert decide(metrics, TECH_OK, POLICY)['status'] == 'rejected'


def test_modele_non_rechargeable_refuse():
    decision = decide(GOOD, {**TECH_OK, 'reload_identical': False}, POLICY)
    assert decision['status'] == 'rejected' and 'reload_identical' in decision['reason']


def test_protocole_incompatible_refuse():
    assert decide(GOOD, {**TECH_OK, 'qualification_corpus_matches': False}, POLICY)['status'] == 'rejected'


def test_donnees_de_demonstration():
    assert decide(GOOD, {**TECH_OK, 'provenance_real': False}, POLICY)['status'] == 'demo-only'


def test_regle_de_mise_en_service():
    assert should_replace_champion(0.80, None, POLICY)[0] is True
    assert should_replace_champion(0.797, 0.80, POLICY)[0] is True       # non-infériorité (marge 0,005)
    assert should_replace_champion(0.79, 0.80, POLICY)[0] is False


def test_metriques_de_classification():
    y = np.array([0, 0, 1, 1])
    metrics = classification_metrics(y, np.array([0.1, 0.4, 0.35, 0.8]), 0.5)
    assert metrics['roc_auc'] == pytest.approx(0.75)
    assert metrics['recall'] == pytest.approx(0.5)
    assert metrics['pr_auc_lift'] == pytest.approx(metrics['pr_auc'] / 0.5)
