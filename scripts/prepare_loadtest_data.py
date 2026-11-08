"""Construire à l'avance les clients des tirs de charge (500 lignes du corpus de qualification, sans la cible)."""
import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RENAME = {'emp.var.rate': 'emp_var_rate', 'cons.price.idx': 'cons_price_idx', 'cons.conf.idx': 'cons_conf_idx',
          'nr.employed': 'nr_employed'}
FIELDS = ['age', 'job', 'marital', 'education', 'default', 'housing', 'loan', 'contact', 'month', 'day_of_week',
          'campaign', 'pdays', 'previous', 'poutcome', 'emp_var_rate', 'cons_price_idx', 'cons_conf_idx',
          'euribor3m', 'nr_employed']


def main():
    frame = pd.read_csv(ROOT / 'data' / 'prepared' / 'qualification.csv').sample(500, random_state=42)
    frame = frame.rename(columns=RENAME)
    clients = [{'client_ref': f"q{row['client_id']}", **{k: row[k] for k in FIELDS}}
               for row in json.loads(frame.to_json(orient='records'))]
    target = ROOT / 'data' / 'loadtest' / 'clients.json'
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(clients), encoding='utf-8')
    print(f'{len(clients)} clients écrits dans {target.relative_to(ROOT)}')


if __name__ == '__main__':
    main()
