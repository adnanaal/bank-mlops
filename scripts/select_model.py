"""Sélection manuelle, retour arrière et historique de l'alias champion.

La sélection vérifie la qualification calculée selon la politique courante (un tag saisi à la main ne suffit pas)
et trace l'acteur, la date, le motif, l'ancienne et la nouvelle version.

Usage :
  uv run --locked python scripts/select_model.py show
  uv run --locked python scripts/select_model.py select --version 6 --reason "retour à la version validée"
  uv run --locked python scripts/select_model.py rollback --reason "dérive constatée sur v7"
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import registry, tracking  # noqa: E402
from src.config import load_config  # noqa: E402
from src.ledger import file_sha256  # noqa: E402
from src.qualification import load_policy  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['show', 'select', 'rollback'])
    parser.add_argument('--version', type=int)
    parser.add_argument('--reason', default='sélection manuelle')
    parser.add_argument('--config', default=str(ROOT / 'configs' / 'config.json'))
    args = parser.parse_args()
    config = load_config(args.config)
    policy = load_policy(config['_paths']['policy'])
    name = config['mlflow']['registered_model']
    alias = policy['selection']['alias']
    if args.action == 'select':
        if not args.version:
            sys.exit('--version requis')
        entry = registry.select(name, args.version, policy, reason=args.reason,
                                qualification_sha=file_sha256(config['_paths']['qualification']))
        print(json.dumps(entry, ensure_ascii=False, indent=2))
    elif args.action == 'rollback':
        print(json.dumps(registry.rollback(name, policy, reason=args.reason), ensure_ascii=False, indent=2))
    current = registry.get_alias_version(name, alias)
    print(f"\nAlias {alias} -> version {current.version if current else 'aucune'}")
    client = tracking.client()
    print('\nVersions :')
    for mv in sorted(client.search_model_versions(f"name='{name}'"), key=lambda v: int(v.version)):
        tags = mv.tags or {}
        print(f"  v{mv.version:>3}  {tags.get('qualification', '?'):17} AUC={tags.get('qualification.roc_auc', '-'):9}"
              f"  lot={tags.get('batch_id', '-'):26} {tags.get('selection', '')[:70]}")
    print('\nHistorique des sélections :')
    for entry in registry.read_history(name, alias):
        print(f"  {entry['at'][:19]}  v{entry['previous_version']} -> v{entry['new_version']}  "
              f"par {entry['actor']} : {entry['reason']}")


if __name__ == '__main__':
    try:
        main()
    except registry.SelectionRefused as refused:
        sys.exit(f'REFUS : {refused}')
