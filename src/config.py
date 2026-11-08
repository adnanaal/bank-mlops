"""Lecture de la configuration JSON ; tous les chemins sont résolus depuis le fichier de configuration."""
import json
from pathlib import Path

REQUIRED_KEYS = ['data', 'output_root', 'seed', 'test_size', 'model', 'decision_threshold', 'quality',
                 'mlflow', 'qualification_policy']


def load_config(config_path):
    config_path = Path(config_path).resolve()
    config = json.loads(config_path.read_text(encoding='utf-8'))
    missing = [key for key in REQUIRED_KEYS if key not in config]
    if missing:
        raise ValueError(f'Clés absentes de la configuration {config_path.name} : {missing}')
    base = (config_path.parent / config.get('base_dir', '.')).resolve()
    paths = {name: (base / value).resolve() for name, value in config['data'].items()}
    paths['output_root'] = (base / config['output_root']).resolve()
    paths['policy'] = (base / config['qualification_policy']).resolve()
    paths['base'] = base
    paths['accepted_dir'] = (base / 'data' / 'accepted').resolve()
    config['_config_path'] = str(config_path)
    config['_paths'] = {key: str(value) for key, value in paths.items()}
    return config


def path(config, name):
    return Path(config['_paths'][name])


def public_view(config):
    """Configuration résolue, sans clés internes, pour la journaliser dans MLflow."""
    return {key: value for key, value in config.items() if not key.startswith('_')} | {
        'resolved_paths': config['_paths'], 'config_file': config['_config_path']}
