"""Exécuter chaque requête du dashboard via l'API Grafana et vérifier qu'elle réussit et renvoie des données.

Les variables de dashboard sont remplacées par « toutes les valeurs » ; les macros ($__timeFilter, $__interval…)
sont interprétées par Grafana lui-même.
Usage : uv run --locked python grafana/check_dashboard.py --hours 48
"""
import argparse
import json
from pathlib import Path

import httpx

GRAFANA = 'http://127.0.0.1:3000'
AUTH = ('admin', '5mlde-local-admin')
ALL_CAMPAIGNS = 'SELECT DISTINCT campaign_id FROM tracing.api_requests'
ALL_ENDPOINTS = "'/predict', '/predict/batch', '/feedback'"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--hours', type=int, default=48)
    args = parser.parse_args()
    dashboard = json.loads((Path(__file__).parent / 'dashboards' / 'bank-api.json').read_text(encoding='utf-8'))
    failures = 0
    for panel in dashboard['panels']:
        for target in panel.get('targets', []):
            sql = target['rawSql'].replace('$campaign', ALL_CAMPAIGNS).replace('$endpoint', ALL_ENDPOINTS)
            body = {'from': f'now-{args.hours}h', 'to': 'now', 'queries': [{
                'refId': 'A', 'datasource': target['datasource'], 'rawSql': sql, 'format': target['format'],
                'intervalMs': 60000, 'maxDataPoints': 500}]}
            response = httpx.post(f'{GRAFANA}/api/ds/query', json=body, auth=AUTH, timeout=30)
            result = response.json().get('results', {}).get('A', {})
            error = result.get('error') or (None if response.status_code == 200 else response.text[:200])
            frames = result.get('frames', [])
            rows = sum(len((f.get('data', {}).get('values') or [[]])[0]) for f in frames)
            status = 'ERREUR' if error else ('OK' if rows else 'VIDE')
            failures += bool(error)
            print(f"{status:6} {rows:6} lignes  #{panel['id']:<3} {panel['title'][:70]}" + (f'  -> {error}' if error else ''))
    raise SystemExit(1 if failures else 0)


if __name__ == '__main__':
    main()
