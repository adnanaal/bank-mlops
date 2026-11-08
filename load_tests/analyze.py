"""Analyser la campagne : tableau workers × utilisateurs × scénario, capacité soutenable et graphiques.

Usage : uv run --locked python load_tests/analyze.py
Sorties : docs/charge/resultats.md, docs/charge/resultats.csv, docs/charge/*.png
"""
import json
from pathlib import Path

import matplotlib

matplotlib.use('Agg')
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / 'load_tests' / 'resultats'
OUT = ROOT / 'docs' / 'charge'
SLO = {'/predict': {'p95_ms': 300, 'fail_pct': 1.0}, '/predict/batch [20]': {'p95_ms': 1000, 'fail_pct': 1.0},
       '/feedback': {'p95_ms': 300, 'fail_pct': 1.0}}
CLIENTS_PER_REQUEST = {'/predict': 1, '/predict/batch [20]': 20, '/feedback': 0}


def md(frame):
    """Tableau Markdown sans dépendance supplémentaire."""
    cols = list(frame.columns)
    out = ['| ' + ' | '.join(map(str, cols)) + ' |', '|' + '---|' * len(cols)]
    out += ['| ' + ' | '.join('' if pd.isna(v) else str(v) for v in row) + ' |'
            for row in frame.itertuples(index=False)]
    return '\n'.join(out)


def load():
    rows = []
    for line in (RESULTS / 'index.jsonl').read_text(encoding='utf-8').splitlines():
        record = json.loads(line)
        stats_path = RESULTS / f"{record['campaign']}_stats.csv"
        if not stats_path.exists():
            continue
        stats = pd.read_csv(stats_path)
        for _, s in stats[stats['Name'] != 'Aggregated'].iterrows():
            count = int(s['Request Count'])
            if count == 0:
                continue
            name = s['Name']
            rows.append({
                'campaign': record['campaign'], 'workers': record['workers'], 'scenario': record['scenario'],
                'users': record['users'], 'repetition': record['repetition'], 'endpoint': name,
                'requests': count, 'fail_pct': round(100 * s['Failure Count'] / count, 2),
                'rps': round(float(s['Requests/s']), 1),
                'clients_per_s': round(float(s['Requests/s']) * CLIENTS_PER_REQUEST.get(name, 0), 1),
                'p50_ms': float(s['50%']), 'p95_ms': float(s['95%']), 'p99_ms': float(s['99%']),
                'api_cpu_pct': record.get('api_cpu_pct_avg'), 'api_rss_mb': record.get('api_rss_mb_avg'),
                'host_cpu_pct': record.get('host_cpu_pct_avg'), 'rss_idle_mb': record.get('rss_idle_mb'),
            })
    frame = pd.DataFrame(rows)
    frame['slo_ok'] = [r.p95_ms < SLO[r.endpoint]['p95_ms'] and r.fail_pct < SLO[r.endpoint]['fail_pct']
                       for r in frame.itertuples()]
    return frame


def capacity(frame):
    """Plus grand débit soutenu respectant l'objectif, par configuration (médiane des répétitions)."""
    main = frame[frame['scenario'].isin(['unit', 'batch20'])]
    grouped = main.groupby(['workers', 'scenario', 'endpoint', 'users'], as_index=False).agg(
        rps=('rps', 'median'), clients_per_s=('clients_per_s', 'median'), p95_ms=('p95_ms', 'median'),
        fail_pct=('fail_pct', 'max'), slo_ok=('slo_ok', 'all'), repetitions=('repetition', 'count'),
        api_cpu_pct=('api_cpu_pct', 'median'), api_rss_mb=('api_rss_mb', 'median'))
    rows = []
    for (workers, scenario), group in grouped.groupby(['workers', 'scenario']):
        ok = group[group['slo_ok']]
        best = ok.sort_values('clients_per_s' if scenario == 'batch20' else 'rps').iloc[-1] if len(ok) else None
        saturated = (~group['slo_ok']).any()
        rows.append({
            'workers': workers, 'scenario': scenario,
            'palier_retenu_users': int(best['users']) if best is not None else None,
            'req_s': best['rps'] if best is not None else None,
            'clients_s': best['clients_per_s'] if best is not None else None,
            'p95_ms': best['p95_ms'] if best is not None else None,
            'repetitions': int(best['repetitions']) if best is not None else 0,
            'api_cpu_pct': best['api_cpu_pct'] if best is not None else None,
            'api_rss_mb': best['api_rss_mb'] if best is not None else None,
            'statut': 'saturation observée au palier suivant' if saturated else 'borne inférieure (aucun palier dégradé)',
        })
    return grouped, pd.DataFrame(rows)


def charts(grouped):
    OUT.mkdir(parents=True, exist_ok=True)
    for scenario, endpoint, limit in [('unit', '/predict', 300), ('batch20', '/predict/batch [20]', 1000)]:
        data = grouped[(grouped['scenario'] == scenario) & (grouped['endpoint'] == endpoint)]
        if data.empty:
            continue
        fig, (left, right) = plt.subplots(1, 2, figsize=(11, 4))
        for workers, group in data.groupby('workers'):
            group = group.sort_values('users')
            left.plot(group['users'], group['p95_ms'], marker='o', label=f'{workers} worker(s)')
            metric = 'clients_per_s' if scenario == 'batch20' else 'rps'
            right.plot(group['users'], group[metric], marker='o', label=f'{workers} worker(s)')
        left.axhline(limit, color='grey', linestyle='--', linewidth=1, label=f'objectif p95 {limit} ms')
        left.set(title=f'{endpoint} : latence p95 client', xlabel='utilisateurs virtuels', ylabel='ms')
        right.set(title=f'{endpoint} : débit', xlabel='utilisateurs virtuels',
                  ylabel='clients scorés / s' if scenario == 'batch20' else 'requêtes / s')
        for axis in (left, right):
            axis.grid(alpha=0.3)
            axis.legend(fontsize=8)
        fig.tight_layout()
        fig.savefig(OUT / f'charge_{scenario}.png', dpi=120)
        plt.close(fig)


def main():
    frame = load()
    grouped, cap = capacity(frame)
    charts(grouped)
    frame.to_csv(OUT / 'resultats.csv', index=False)
    mixed = frame[frame['scenario'] == 'mixed']
    lines = ['# Résultats des tests de charge', '', 'Protocole et objectifs : voir `protocole.md`.', '',
             '## Capacité soutenable par configuration', '', md(cap), '',
             '## Matrice complète (médiane des répétitions)', '',
             md(grouped.drop(columns=['scenario'])), '']
    if not mixed.empty:
        lines += ['## Scénario mixte (70 % unitaire, 20 % lot, 10 % feedback)', '',
                  md(mixed[['workers', 'users', 'endpoint', 'requests', 'rps', 'p50_ms', 'p95_ms', 'p99_ms',
                            'fail_pct']]), '']
    lines += ['![latence et débit unitaires](charge_unit.png)', '', '![latence et débit par lot](charge_batch20.png)']
    (OUT / 'resultats.md').write_text('\n'.join(lines), encoding='utf-8')
    print(cap.to_string(index=False))


if __name__ == '__main__':
    main()
