"""Campagne de charge reproductible : 1, 2 puis 4 workers Uvicorn × paliers d'utilisateurs × scénarios.

Pour chaque configuration : démarrage propre de l'API, attente de /health/ready, chauffe non mesurée, puis tirs
Locust (nouvel identifiant de campagne par tir) avec échantillonnage CPU/RAM des processus de l'API et du poste.
Arrêter l'API manuelle, les plannings Prefect et Grafana pendant la série (charges parasites).

Usage :
  uv run --locked python load_tests/run_campaign.py                                  # matrice complète
  uv run --locked python load_tests/run_campaign.py --workers 2 --scenarios unit --users 20 40 --repeat 3
"""
import argparse
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
import psutil

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / 'load_tests' / 'resultats'
HOST = 'http://127.0.0.1:8001'


def start_api(workers):
    env = {**os.environ, 'PYTHONWARNINGS': 'ignore'}
    log = open(RESULTS / f'uvicorn_w{workers}.log', 'w', encoding='utf-8')  # noqa: SIM115
    process = subprocess.Popen(
        [sys.executable, '-m', 'uvicorn', 'api_prediction.main:app', '--host', '127.0.0.1', '--port', '8001',
         '--workers', str(workers), '--log-level', 'warning'], cwd=ROOT, env=env, stdout=log, stderr=log)
    deadline = time.time() + 180
    ready_instances = set()
    while time.time() < deadline:
        try:
            response = httpx.get(f'{HOST}/health/ready', timeout=3)
            if response.status_code == 200:
                ready_instances.add(response.json()['instance_id'])
                if len(ready_instances) >= workers or time.time() > deadline - 150:
                    return process
        except httpx.HTTPError:
            pass
        time.sleep(1)
    stop_api(process)
    raise RuntimeError(f'API non prête avec {workers} worker(s)')


def stop_api(process):
    try:
        parent = psutil.Process(process.pid)
        for child in parent.children(recursive=True):
            child.kill()
        parent.kill()
    except psutil.NoSuchProcess:
        pass
    process.wait(timeout=30)
    time.sleep(3)


def api_processes(process):
    try:
        parent = psutil.Process(process.pid)
        return [parent] + parent.children(recursive=True)
    except psutil.NoSuchProcess:
        return []


class Sampler(threading.Thread):
    """Échantillonne chaque seconde CPU (%) et RSS (Mo) des processus de l'API, et la CPU du poste."""

    def __init__(self, process):
        super().__init__(daemon=True)
        self.process, self.samples, self.stop_event = process, [], threading.Event()

    def run(self):
        procs = api_processes(self.process)
        for p in procs:
            p.cpu_percent(None)
        psutil.cpu_percent(None)
        while not self.stop_event.wait(1.0):
            cpu, rss = 0.0, 0.0
            for p in procs:
                try:
                    cpu += p.cpu_percent(None)
                    rss += p.memory_info().rss / 2 ** 20
                except psutil.NoSuchProcess:
                    continue
            self.samples.append({'api_cpu_pct': cpu, 'api_rss_mb': rss, 'host_cpu_pct': psutil.cpu_percent(None)})

    def summary(self):
        steady = self.samples[5:] or self.samples
        if not steady:
            return {}
        def avg(key):
            return round(sum(s[key] for s in steady) / len(steady), 1)
        return {'api_cpu_pct_avg': avg('api_cpu_pct'), 'api_rss_mb_avg': avg('api_rss_mb'),
                'host_cpu_pct_avg': avg('host_cpu_pct'), 'api_cpu_pct_max': max(s['api_cpu_pct'] for s in steady)}


def run_locust(process, campaign, scenario, users, duration):
    env = {**os.environ, 'LOCUST_SCENARIO': scenario, 'LOCUST_CAMPAIGN': campaign, 'PYTHONWARNINGS': 'ignore'}
    sampler = Sampler(process)
    sampler.start()
    completed = subprocess.run(
        [sys.executable, '-m', 'locust', '-f', str(ROOT / 'load_tests' / 'locustfile.py'), '--host', HOST,
         '--headless', '-u', str(users), '-r', str(users), '-t', f'{duration}s', '--csv', str(RESULTS / campaign),
         '--only-summary', '--stop-timeout', '5'], cwd=ROOT, env=env, capture_output=True, text=True)
    sampler.stop_event.set()
    sampler.join()
    return {'returncode': completed.returncode, **sampler.summary(),
            'locust_warnings': [line for line in completed.stderr.splitlines() if 'CPU usage' in line][:3]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workers', type=int, nargs='+', default=[1, 2, 4])
    parser.add_argument('--scenarios', nargs='+', default=['unit', 'batch20'])
    parser.add_argument('--users', type=int, nargs='+', default=[5, 10, 20, 40])
    parser.add_argument('--duration', type=int, default=60)
    parser.add_argument('--repeat', type=int, default=1)
    parser.add_argument('--mixed-users', type=int, default=20, help='Palier du scénario mixte (0 = aucun)')
    args = parser.parse_args()
    RESULTS.mkdir(parents=True, exist_ok=True)
    index_path = RESULTS / 'index.jsonl'
    manifest = json.loads((ROOT / 'configs' / 'service_manifest.json').read_text(encoding='utf-8'))
    for workers in args.workers:
        process = start_api(workers)
        rss_idle = round(sum(p.memory_info().rss for p in api_processes(process)) / 2 ** 20, 1)
        print(f'API prête : {workers} worker(s), {len(api_processes(process))} processus, {rss_idle} Mo au repos')
        run_locust(process, f'warmup-w{workers}', 'unit', 5, 15)
        plan = [(s, u) for s in args.scenarios for u in args.users]
        if args.mixed_users:
            plan.append(('mixed', args.mixed_users))
        for scenario, users in plan:
            for repetition in range(1, args.repeat + 1):
                stamp = datetime.now(timezone.utc).strftime('%H%M%S')
                campaign = f'w{workers}-{scenario}-u{users}-r{repetition}-{stamp}'
                started = datetime.now(timezone.utc).isoformat()
                outcome = run_locust(process, campaign, scenario, users, args.duration)
                record = {'campaign': campaign, 'workers': workers, 'scenario': scenario, 'users': users,
                          'repetition': repetition, 'duration_s': args.duration, 'started_at': started,
                          'model_version': manifest['version'], 'rss_idle_mb': rss_idle, **outcome}
                with index_path.open('a', encoding='utf-8') as handle:
                    handle.write(json.dumps(record) + '\n')
                print(json.dumps(record))
        stop_api(process)


if __name__ == '__main__':
    main()
