"""Producteur simulé : dépose le prochain lot mensuel du catalogue dans la zone d'arrivée (data/landing).

Usage :
  uv run --locked python scripts/simulate_arrival.py            # dépose le prochain lot
  uv run --locked python scripts/simulate_arrival.py --count 3  # dépose les 3 prochains
  uv run --locked python scripts/simulate_arrival.py --period 2009-05
"""
import argparse
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / 'data' / 'catalog'
LANDING = ROOT / 'data' / 'landing'


def next_periods(count):
    delivered = {p.stem for p in LANDING.glob('*.csv')}
    return [p.stem for p in sorted(CATALOG.glob('*.csv')) if p.stem not in delivered][:count]


def deliver(period):
    source = CATALOG / f'{period}.csv'
    if not source.exists():
        raise SystemExit(f'Lot inconnu : {period}')
    LANDING.mkdir(parents=True, exist_ok=True)
    target = LANDING / f'{period}.csv'
    shutil.copyfile(source, target)
    print(f'Lot déposé : {target.relative_to(ROOT)}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--count', type=int, default=1)
    parser.add_argument('--period')
    args = parser.parse_args()
    periods = [args.period] if args.period else next_periods(args.count)
    if not periods:
        print('Aucun lot restant dans le catalogue.')
    for period in periods:
        deliver(period)


if __name__ == '__main__':
    main()
