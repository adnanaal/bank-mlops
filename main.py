"""Point d'entrée de l'entraînement : flow Prefect qui enchaîne uniquement les quatre fonctions mères.

Usage :
  uv run --locked python main.py --config configs/config.json --batch none   # corpus initial seul
  uv run --locked python main.py --config configs/config.json                # prochain lot en attente
  uv run --locked python main.py --config configs/config.json --batch data/landing/2009-06.csv
"""
import argparse

from prefect import flow

from orchestration.hooks import on_crashed, on_failure
from orchestration.tasks import entrainement, evaluation, ingestion, pretraitement


@flow(name='bank-training-pipeline', retries=0, on_failure=[on_failure], on_crashed=[on_crashed])
def main(config_path: str, batch: str = 'next', trigger: str = 'manual'):
    data = ingestion(config_path, batch, trigger)
    prepared = pretraitement(data)
    trained = entrainement(prepared)
    return evaluation(trained)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Entraîner, évaluer et publier le modèle de souscription")
    parser.add_argument('--config', required=True, help='Chemin du fichier de configuration JSON')
    parser.add_argument('--batch', default='next', help="'next' (défaut), 'none' ou chemin d'un lot")
    parser.add_argument('--trigger', default='manual')
    args = parser.parse_args()
    main(args.config, args.batch, args.trigger)
