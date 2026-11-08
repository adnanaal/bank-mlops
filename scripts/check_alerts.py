"""Lister les déclenchements et actions de l'automation d'alerte (preuve côté serveur Prefect).

Usage : uv run --locked python scripts/check_alerts.py --minutes 30
"""
import argparse
import os
from datetime import datetime, timedelta, timezone

import httpx

API = os.environ.get('PREFECT_API_URL', 'http://localhost:4200/api')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--minutes', type=int, default=30)
    args = parser.parse_args()
    since = (datetime.now(timezone.utc) - timedelta(minutes=args.minutes)).isoformat()
    body = {'filter': {'occurred': {'since': since},
                       'event': {'prefix': ['prefect.automation.', 'prefect.flow-run.Failed',
                                            'prefect.flow-run.Crashed', 'prefect.flow-run.Completed']}},
            'limit': 50}
    response = httpx.post(f'{API}/events/filter', json=body, timeout=30)
    if response.status_code >= 400:
        raise SystemExit(f'{response.status_code} {response.text[:800]}')
    for event in sorted(response.json().get('events', []), key=lambda e: e['occurred']):
        resource = event.get('resource', {})
        name = resource.get('prefect.resource.name', '')
        related = {r.get('prefect.resource.role'): r.get('prefect.resource.name') for r in event.get('related', [])}
        detail = event.get('payload', {}).get('reason') or event.get('payload', {}).get('invocation') or ''
        print(f"{event['occurred'][11:19]}  {event['event']:45} {name:28} flow={related.get('flow', '-'):24} {detail}")


if __name__ == '__main__':
    main()
