"""Récupérer bank-additional-full.csv depuis l'UCI ML Repository (ou une copie locale) et tracer sa provenance.

Usage :
  uv run --locked python scripts/download_data.py
  uv run --locked python scripts/download_data.py --from-local "chemin/vers/bank-additional-full.csv"
"""
import argparse
import hashlib
import io
import json
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import httpx

UCI_URL = 'https://archive.ics.uci.edu/static/public/222/bank+marketing.zip'
TARGET_NAME = 'bank-additional-full.csv'
EXPECTED_ROWS = 41_188
ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / 'data' / 'raw'


def _find_member(archive, name):
    for member in archive.namelist():
        if member.endswith('/' + name) or member == name:
            if '__MACOSX' not in member:
                return member
    return None


def extract_from_zip(content):
    """L'archive UCI contient des zips imbriqués : bank-additional.zip contient le CSV complet."""
    with zipfile.ZipFile(io.BytesIO(content)) as outer:
        member = _find_member(outer, TARGET_NAME)
        if member:
            return outer.read(member)
        for inner_name in outer.namelist():
            if inner_name.endswith('.zip'):
                found = extract_from_zip(outer.read(inner_name))
                if found:
                    return found
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--from-local', help='Copier un CSV déjà téléchargé au lieu de le télécharger')
    args = parser.parse_args()
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    destination = RAW_DIR / TARGET_NAME
    if args.from_local:
        data = Path(args.from_local).read_bytes()
        origin = f'copie locale : {Path(args.from_local).name}'
    else:
        response = httpx.get(UCI_URL, timeout=120, follow_redirects=True)
        response.raise_for_status()
        data = extract_from_zip(response.content)
        if data is None:
            raise SystemExit(f'{TARGET_NAME} introuvable dans {UCI_URL}')
        origin = UCI_URL
    rows = data.count(b'\n') - 1
    if rows != EXPECTED_ROWS:
        raise SystemExit(f'Nombre de lignes inattendu : {rows} (attendu {EXPECTED_ROWS})')
    destination.write_bytes(data)
    provenance = {
        'dataset': 'Bank Marketing (bank-additional-full.csv)',
        'citation': 'Moro, S., Rita, P., & Cortez, P. (2014). Bank Marketing [Dataset]. '
                    'UCI Machine Learning Repository. https://doi.org/10.24432/C5K306',
        'license': 'CC BY 4.0',
        'origin': origin,
        'retrieved_at': datetime.now(timezone.utc).isoformat(),
        'sha256': hashlib.sha256(data).hexdigest(),
        'bytes': len(data),
        'rows': rows,
        'note': 'Lignes ordonnées par date de mai 2008 à novembre 2010 ; séparateur ";".',
    }
    (RAW_DIR / 'SOURCE.json').write_text(json.dumps(provenance, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(provenance, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    shutil.disk_usage(ROOT)
    main()
