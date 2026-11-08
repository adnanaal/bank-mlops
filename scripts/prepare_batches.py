"""Découper le CSV historique pour simuler l'arrivée de nouvelles données (rejeu déclaré).

- client_id : numéro de ligne dans le fichier source (traçabilité, pas une feature).
- period : mois AAAA-MM reconstitué (lignes ordonnées par date ; l'année change quand le mois recule).
- Corpus de qualification : ~15 % des lignes de chaque mois, tirées par hachage stable de client_id,
  jamais utilisées pour entraîner ; il sert à comparer les versions dans des conditions identiques.
- Corpus initial : mai à décembre 2008 (hors qualification).
- Lots mensuels : chaque mois de mars 2009 à novembre 2010 (hors qualification), à faire arriver un par un.

Usage : uv run --locked python scripts/prepare_batches.py
"""
import hashlib
import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / 'data' / 'raw' / 'bank-additional-full.csv'
PREPARED = ROOT / 'data' / 'prepared'
CATALOG = ROOT / 'data' / 'catalog'          # lots disponibles côté « producteur »
INITIAL_LAST_PERIOD = '2008-12'
QUALIFICATION_SHARE = 15                      # en pourcents
SALT = 'bank-mlops-qualification-v1'
MONTHS = ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec']


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def add_periods(frame, start_year=2008):
    month_numbers = frame['month'].map({m: i + 1 for i, m in enumerate(MONTHS)})
    years, year, previous = [], start_year, int(month_numbers.iloc[0])
    for value in month_numbers.astype(int):
        if value < previous:
            year += 1
        years.append(year)
        previous = value
    return [f'{y}-{m:02d}' for y, m in zip(years, month_numbers.astype(int), strict=False)]


def in_qualification(client_id):
    digest = hashlib.sha256(f'{SALT}:{client_id}'.encode()).hexdigest()
    return int(digest, 16) % 100 < QUALIFICATION_SHARE


def write_csv(frame, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False, lineterminator='\n')
    return {'path': str(path.relative_to(ROOT)).replace('\\', '/'), 'rows': int(len(frame)),
            'positive_rate': round(float((frame['y'] == 'yes').mean()), 4), 'sha256': sha256_file(path)}


def main():
    raw = pd.read_csv(RAW, sep=';')
    raw.insert(0, 'client_id', range(1, len(raw) + 1))
    raw.insert(1, 'period', add_periods(raw))
    qualification_mask = raw['client_id'].map(in_qualification)
    qualification = raw[qualification_mask]
    trainable = raw[~qualification_mask]
    manifest = {
        'source_sha256': sha256_file(RAW),
        'qualification_rule': f'sha256("{SALT}:client_id") % 100 < {QUALIFICATION_SHARE}',
        'initial_periods': f'2008-05 .. {INITIAL_LAST_PERIOD}',
        'qualification': write_csv(qualification, PREPARED / 'qualification.csv'),
        'initial': write_csv(trainable[trainable['period'] <= INITIAL_LAST_PERIOD], PREPARED / 'initial.csv'),
        'batches': [],
    }
    later = trainable[trainable['period'] > INITIAL_LAST_PERIOD]
    for period, batch in later.groupby('period', sort=True):
        manifest['batches'].append({'period': period, **write_csv(batch, CATALOG / f'{period}.csv')})
    (PREPARED / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f"qualification={manifest['qualification']['rows']} initial={manifest['initial']['rows']} "
          f"lots={len(manifest['batches'])} ({manifest['batches'][0]['period']} .. {manifest['batches'][-1]['period']})")


if __name__ == '__main__':
    main()
