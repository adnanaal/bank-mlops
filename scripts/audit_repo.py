"""Audit avant commit : fichiers qui partiraient dans Git, taille, secrets et données.

Usage : uv run --locked python scripts/audit_repo.py
"""
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SECRET_PATTERNS = {
    'webhook Teams / Power Automate': re.compile(r'(logic\.azure\.com|powerplatform\.com|webhook\.office\.com)', re.I),
    'signature d\'URL': re.compile(r'[?&]sig=[A-Za-z0-9%_-]{20,}'),
    'clé privée': re.compile(r'-----BEGIN [A-Z ]*PRIVATE KEY-----'),
    'jeton GitHub': re.compile(r'gh[pousr]_[A-Za-z0-9]{30,}'),
}


def main():
    listing = subprocess.run(['git', 'ls-files', '--others', '--cached', '--exclude-standard'], cwd=ROOT,
                             capture_output=True, text=True, check=True).stdout.split()
    total, problems = 0, []
    for name in listing:
        path = ROOT / name
        size = path.stat().st_size
        total += size
        if size > 1_000_000:
            problems.append(f'fichier volumineux : {name} ({size / 1e6:.1f} Mo)')
        if name.startswith(('data/', 'runs/', '.venv/')) or name in ('.env',):
            problems.append(f'chemin interdit : {name}')
        if path.suffix in {'.png', '.jpg'}:
            continue
        text = path.read_text(encoding='utf-8', errors='ignore')
        for label, pattern in SECRET_PATTERNS.items():
            if pattern.search(text):
                problems.append(f'{label} détecté dans {name}')
        if name.endswith('.csv') and 'client_id' in text.splitlines()[0]:
            problems.append(f'données client dans {name}')
    print(f'{len(listing)} fichiers, {total / 1e6:.2f} Mo au total')
    print('\n'.join(problems) if problems else 'Aucun secret, aucune donnée, aucun fichier volumineux.')
    raise SystemExit(1 if problems else 0)


if __name__ == '__main__':
    main()
