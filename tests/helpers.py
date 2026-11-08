"""Données synthétiques conformes au contrat (les tests ne dépendent pas des CSV, absents de Git)."""
import json
from pathlib import Path

import numpy as np
import pandas as pd

from src.contract import BATCH_COLUMNS, CATEGORY_DOMAINS

QUALITY = {
    'min_batch_rows': 100, 'max_duplicate_share': 0.01,
    'max_unknown_share': {'job': 0.10, 'marital': 0.05, 'education': 0.20, 'default': 0.40,
                          'housing': 0.10, 'loan': 0.10},
    'min_positive_in_snapshot': 50,
}


def make_frame(n, start_id=1, period='2009-05', seed=0, positive_rate=0.3):
    rng = np.random.default_rng(seed)
    month = ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'][
        int(period[-2:]) - 1]

    def pick(column):
        domain = [v for v in CATEGORY_DOMAINS[column] if v != 'unknown']
        return rng.choice(domain, size=n)

    frame = pd.DataFrame({
        'client_id': np.arange(start_id, start_id + n),
        'period': period,
        'age': rng.integers(18, 90, n),
        'job': pick('job'), 'marital': pick('marital'), 'education': pick('education'),
        'default': pick('default'), 'housing': pick('housing'), 'loan': pick('loan'),
        'contact': pick('contact'), 'month': month, 'day_of_week': pick('day_of_week'),
        'duration': rng.integers(0, 1000, n),
        'campaign': rng.integers(1, 10, n),
        'pdays': np.where(rng.random(n) < 0.9, 999, rng.integers(0, 30, n)),
        'previous': rng.integers(0, 3, n),
        'poutcome': pick('poutcome'),
        'emp.var.rate': rng.uniform(-3, 1.4, n).round(1),
        'cons.price.idx': rng.uniform(92.2, 94.7, n).round(3),
        'cons.conf.idx': rng.uniform(-50, -27, n).round(1),
        'euribor3m': rng.uniform(0.6, 5, n).round(3),
        'nr.employed': rng.uniform(4964, 5228, n).round(1),
    })
    signal = (frame['pdays'] != 999) | (frame['euribor3m'] < 1.5)
    probability = np.where(signal, 0.75, positive_rate / 2)
    frame['y'] = np.where(rng.random(n) < probability, 'yes', 'no')
    return frame[BATCH_COLUMNS]


def write_project(tmp_path, n_initial=600, n_batch=150, n_qualification=300):
    """Arborescence de projet minimale avec configuration, corpus et un lot en attente."""
    data = tmp_path / 'data'
    (data / 'prepared').mkdir(parents=True)
    (data / 'landing').mkdir()
    (data / 'raw').mkdir()
    make_frame(n_initial, start_id=1, period='2008-11', seed=1).to_csv(data / 'prepared' / 'initial.csv', index=False)
    make_frame(n_qualification, start_id=100_000, period='2009-01', seed=2).to_csv(
        data / 'prepared' / 'qualification.csv', index=False)
    make_frame(n_batch, start_id=10_000, period='2009-05', seed=3).to_csv(data / 'landing' / '2009-05.csv',
                                                                            index=False)
    (data / 'raw' / 'SOURCE.json').write_text(json.dumps({'sha256': 'synthetique'}), encoding='utf-8')
    configs = tmp_path / 'configs'
    configs.mkdir()
    policy_source = Path(__file__).resolve().parents[1] / 'configs' / 'qualification_policy.json'
    (configs / 'qualification_policy.json').write_text(policy_source.read_text(encoding='utf-8'), encoding='utf-8')
    config = json.loads((Path(__file__).resolve().parents[1] / 'configs' / 'config.json').read_text(encoding='utf-8'))
    config['quality'] = QUALITY
    config_path = configs / 'config.json'
    config_path.write_text(json.dumps(config), encoding='utf-8')
    return config_path
