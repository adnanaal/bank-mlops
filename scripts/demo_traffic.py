"""Trafic de démonstration : prédictions unitaires et par lot, puis feedbacks avec labels observés.

Les clients viennent du corpus de qualification : leur label historique réel (y) sert de label observé
(label_source = "uci-historique"), ce qui alimente le panneau « qualité observée » sans label inventé.

Usage : uv run --locked python scripts/demo_traffic.py --units 60 --batches 5 --feedback-share 0.4
"""
import argparse
import random
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
API = 'http://127.0.0.1:8001'
RENAME = {'emp.var.rate': 'emp_var_rate', 'cons.price.idx': 'cons_price_idx', 'cons.conf.idx': 'cons_conf_idx',
          'nr.employed': 'nr_employed'}
FIELDS = ['age', 'job', 'marital', 'education', 'default', 'housing', 'loan', 'contact', 'month', 'day_of_week',
          'campaign', 'pdays', 'previous', 'poutcome', 'emp_var_rate', 'cons_price_idx', 'cons_conf_idx',
          'euribor3m', 'nr_employed']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--units', type=int, default=60)
    parser.add_argument('--batches', type=int, default=5)
    parser.add_argument('--feedback-share', type=float, default=0.4)
    parser.add_argument('--campaign', default='demo-nominal')
    args = parser.parse_args()
    rng = random.Random(7)
    frame = pd.read_csv(ROOT / 'data' / 'prepared' / 'qualification.csv').rename(columns=RENAME).sample(
        args.units + 20 * args.batches, random_state=7)
    rows = frame.to_dict(orient='records')
    labels = {f"q{r['client_id']}": r['y'] for r in rows}
    clients = [{'client_ref': f"q{r['client_id']}", **{k: r[k] for k in FIELDS}} for r in rows]
    headers = {'X-Test-Campaign': args.campaign}
    predictions, versions = [], set()
    with httpx.Client(base_url=API, headers=headers, timeout=30) as http:
        for client in clients[:args.units]:
            body = http.post('/predict', json={'client': client}).raise_for_status().json()
            versions.add(body['model']['version'])
            predictions.append(body['prediction'])
        rest = clients[args.units:]
        for i in range(args.batches):
            body = http.post('/predict/batch', json={'clients': rest[20 * i:20 * (i + 1)]}).raise_for_status().json()
            predictions.extend(body['predictions'])
        statuses = {}
        for prediction in predictions:
            if rng.random() >= args.feedback_share:
                continue
            observed = labels[prediction['client_ref']]
            payload = {'feedback_id': str(uuid.uuid4()), 'prediction_id': prediction['prediction_id'],
                       'useful': prediction['label'] == observed, 'observed_label': observed,
                       'observed_at': datetime.now(timezone.utc).isoformat(), 'label_source': 'uci-historique'}
            status = http.post('/feedback', json=payload).status_code
            statuses[status] = statuses.get(status, 0) + 1
        invalid = http.post('/predict', json={'client': {**clients[0], 'age': -5}})
    correct = sum(p['label'] == labels[p['client_ref']] for p in predictions)
    print(f'Version(s) servie(s) : {sorted(versions)}')
    print(f'Prédictions : {len(predictions)} ({args.units} unitaires + {args.batches} lots de 20)')
    print(f'Feedbacks : {statuses} ; accord avec le label historique : {correct}/{len(predictions)}')
    print(f'Requête invalide volontaire : HTTP {invalid.status_code} {invalid.json()["error_code"]}')


if __name__ == '__main__':
    main()
