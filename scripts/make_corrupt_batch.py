"""Incident contrôlé : dépose dans data/landing un lot non conforme, dérivé d'un lot réel du catalogue.

Défauts injectés (chacun violant une règle bloquante du contrat) :
  - âges négatifs sur quelques lignes ;
  - campaign = "inconnu" (texte dans une colonne numérique) ;
  - 60 % de job = "unknown" (au-delà du seuil de qualité) ;
  - client_id dupliqués.
Le CSV source du catalogue n'est pas modifié.

Usage : uv run --locked python scripts/make_corrupt_batch.py --period 2009-06
"""
import argparse
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]


def corrupt(frame):
    bad = frame.copy()
    bad.loc[bad.index[:5], 'age'] = -3
    bad['campaign'] = bad['campaign'].astype(object)
    bad.loc[bad.index[5:8], 'campaign'] = 'inconnu'
    unknown_rows = bad.sample(frac=0.6, random_state=0).index
    bad.loc[unknown_rows, 'job'] = 'unknown'
    bad.loc[bad.index[10:12], 'client_id'] = bad['client_id'].iloc[0]
    return bad


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--period', default='2009-06')
    args = parser.parse_args()
    source = ROOT / 'data' / 'catalog' / f'{args.period}.csv'
    target = ROOT / 'data' / 'landing' / f'{args.period}-corrompu.csv'
    target.parent.mkdir(parents=True, exist_ok=True)
    corrupt(pd.read_csv(source)).to_csv(target, index=False, lineterminator='\n')
    print(f'Lot corrompu déposé : {target.relative_to(ROOT)}')


if __name__ == '__main__':
    main()
