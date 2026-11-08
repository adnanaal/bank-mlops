"""Construire le manifeste de service : résout UNE FOIS l'alias champion en numéro de version précis.

Tous les workers de l'API chargent ensuite exactement cette version : un alias mutable ne suffit pas
à identifier ce qui a prédit. Le manifeste précédent est archivé (retour arrière = le restaurer).

Usage :
  uv run --locked python scripts/build_manifest.py                  # alias champion
  uv run --locked python scripts/build_manifest.py --version 4      # version précise
  uv run --locked python scripts/build_manifest.py --restore-previous
"""
import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import registry, tracking  # noqa: E402
from src.config import load_config  # noqa: E402

MANIFEST = ROOT / 'configs' / 'service_manifest.json'
ARCHIVE = ROOT / 'configs' / 'manifests'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', default=str(ROOT / 'configs' / 'config.json'))
    parser.add_argument('--version', type=int)
    parser.add_argument('--alias', default='champion')
    parser.add_argument('--restore-previous', action='store_true')
    args = parser.parse_args()
    ARCHIVE.mkdir(parents=True, exist_ok=True)
    if args.restore_previous:
        archived = sorted(ARCHIVE.glob('*.json'))
        if not archived:
            sys.exit('Aucun manifeste archivé')
        shutil.copyfile(archived[-1], MANIFEST)
        print(f'Manifeste restauré depuis {archived[-1].name}')
        print(MANIFEST.read_text(encoding='utf-8'))
        return
    config = load_config(args.config)
    name = config['mlflow']['registered_model']
    client = tracking.client()
    if args.version:
        mv = client.get_model_version(name, str(args.version))
    else:
        mv = registry.get_alias_version(name, args.alias)
        if mv is None:
            sys.exit(f"Aucune version ne porte l'alias {args.alias}")
    tags = mv.tags or {}
    manifest = {
        'registered_name': name, 'version': int(mv.version), 'resolved_from': 'version' if args.version
        else f'alias:{args.alias}', 'mlflow_run_id': mv.run_id, 'model_uri': f'models:/{name}/{mv.version}',
        'source': mv.source, 'tracking_uri': tracking.configure(), 'qualification': tags.get('qualification'),
        'qualification_policy': tags.get('qualification_policy'),
        'decision_threshold': float(config['decision_threshold']),
        'resolved_at': datetime.now(timezone.utc).isoformat(),
    }
    if MANIFEST.exists():
        previous = json.loads(MANIFEST.read_text(encoding='utf-8'))
        shutil.copyfile(MANIFEST, ARCHIVE / f"{previous['resolved_at'][:19].replace(':', '')}_v{previous['version']}.json")
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
