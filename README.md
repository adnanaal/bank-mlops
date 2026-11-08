# bank-mlops — Industrialiser un modèle de souscription bancaire

Projet final 5MLDE. Une chaîne MLOps complète, locale et gratuite, qui estime la **probabilité qu'un client
souscrive un dépôt à terme** après un appel de campagne (UCI Bank Marketing). Le modèle est volontairement simple
(régression logistique) : l'objectif est une chaîne opérationnelle, reproductible et maintenable.

Synthèse des choix et résultats OPS : [SYNTHESE_OPS.md](SYNTHESE_OPS.md).

## Architecture

```text
 catalogue de lots ──simulate_arrival──► data/landing ──► Prefect (déploiement planifié bank-batch-watcher)
                                                              │  flow bank-training-pipeline
                                                              ▼
   ingestion (Great Expectations, bloquant) → prétraitement → entraînement → évaluation/publication
        │ lot rejeté : rapport + ledger + flow Failed → automation Prefect → Teams          │
        ▼                                                                                   ▼
   MLflow : runs, snapshot + SHA-256, modèle, version du registre, tags qualification, alias champion
                                                              │ manifeste (version précise)
                                                              ▼
   API FastAPI :8001 (/predict, /predict/batch, /feedback, /health/*) ──► PostgreSQL (schéma tracing)
                                                              │                       ▲ pipeline_events
                                                              ▼                       │
                                                  Grafana :3000 (usage, erreurs, durées, versions, feedbacks, traitements)
```

| Composant | Rôle | Version |
|---|---|---|
| uv + Python | Environnement verrouillé (`uv.lock`) | Python 3.12 |
| Great Expectations | Contrôles qualité bloquants avant prétraitement | 1.23.2 |
| scikit-learn | Pipeline prétraitement + régression logistique | 1.6.1 |
| MLflow (Docker) | Suivi, registre, tags, alias, retour arrière | 3.16.1 |
| Prefect (Docker) | Flow, tâches, retries, planning, automation d'alerte | 3.8.7 (+ SQLAlchemy 2.0, voir synthèse) |
| FastAPI + psycopg | API d'inférence et traçabilité | — |
| PostgreSQL (Docker) | Traces des requêtes, prédictions, feedbacks, traitements | 18.6 |
| Grafana (Docker) | Dashboard provisionné | 13.2.3 |
| Locust | Tests de charge | 2.46.6 |

## Données : provenance et conditions d'utilisation

- Source : [UCI ML Repository — Bank Marketing](https://archive.ics.uci.edu/dataset/222/bank+marketing),
  fichier `bank-additional-full.csv` (41 188 appels, mai 2008 → novembre 2010, lignes ordonnées par date).
- Citation : Moro, S., Rita, P., & Cortez, P. (2014). *Bank Marketing* [Dataset]. UCI Machine Learning Repository.
  https://doi.org/10.24432/C5K306 — licence **CC BY 4.0**.
- Les CSV ne sont **pas** dans Git : `scripts/download_data.py` les télécharge et écrit `data/raw/SOURCE.json`
  (URL, date, SHA-256).
- **Rejeu déclaré** : l'historique est découpé en un corpus initial (mai–déc. 2008), 19 lots mensuels
  (mars 2009 → nov. 2010) déposés un par un, et un corpus de qualification fixe (~15 % de chaque mois, tiré par
  hachage stable de `client_id`) jamais utilisé pour entraîner.
- `duration` est exclue des features : elle n'est connue qu'après l'appel (fuite signalée par UCI).

## Installation (Windows, PowerShell)

Prérequis : Docker Desktop (moteur WSL 2), uv, Git. Python 3.12 est installé par uv.

```powershell
git clone <url-du-depot> bank-mlops; cd bank-mlops
uv python install 3.12
uv sync --locked
Copy-Item .env.example .env
docker compose up -d --build --wait          # MLflow :7281, Prefect :4200, PostgreSQL :15432, Grafana :3000
. .\scripts\env.ps1                          # variables des clients Python (à faire dans chaque terminal)
uv run --locked python scripts/download_data.py
uv run --locked python scripts/prepare_batches.py
uv run --locked python scripts/prepare_loadtest_data.py
```

Interfaces : MLflow http://127.0.0.1:7281 · Prefect http://127.0.0.1:4200 · Grafana http://127.0.0.1:3000
(`admin` / `5mlde-local-admin`) · API http://127.0.0.1:8001/docs. Utiliser `127.0.0.1` plutôt que `localhost`.

## Commandes

| Étape | Commande |
|---|---|
| Tests et qualité | `uv run --locked pytest` · `uv run --locked ruff check .` |
| Entraînement initial | `uv run --locked python main.py --config configs/config.json --batch none` |
| Déposer des lots | `uv run --locked python scripts/simulate_arrival.py --count 2` |
| Traiter le prochain lot | `uv run --locked python main.py --config configs/config.json` |
| Déploiement planifié | `uv run --locked python orchestration/deploy.py --interval 120` (Ctrl+C pour arrêter) |
| Alerte Teams | URL du webhook dans `data/state/teams_url.txt` puis `uv run --locked python orchestration/setup_alerting.py --test` |
| Versions, sélection, retour arrière | `uv run --locked python scripts/select_model.py show` · `... rollback --reason "..."` |
| Manifeste de service | `uv run --locked python scripts/build_manifest.py` (champion → version précise) |
| API | `uv run --locked uvicorn api_prediction.main:app --host 127.0.0.1 --port 8001 --workers 2` |
| Campagne de charge | `uv run --locked python load_tests/run_campaign.py` puis `uv run --locked python load_tests/analyze.py` |
| Dashboard | `uv run --locked python grafana/build_dashboard.py` (provisionné automatiquement) |

## Démonstrations

1. **Nominal** : `scripts/simulate_arrival.py` dépose un lot → le déploiement planifié le détecte → validation GX,
   run MLflow, version qualifiée, sélection éventuelle → `build_manifest.py` + redémarrage de l'API → requêtes et
   feedbacks visibles dans Grafana.
2. **Lot invalide** : `scripts/make_corrupt_batch.py` → rejet GX avant tout prétraitement, rapport dans
   `data/diagnostics`, lot inscrit `rejected` (jamais rejoué tel quel), flow `Failed`, message Teams, champion inchangé.
3. **Dépendance indisponible** : `scripts/demo_dependency_outage.py` (PostgreSQL arrêté puis relancé).
4. **Retour arrière** : `scripts/select_model.py rollback` puis `build_manifest.py` et redémarrage de l'API.

## Arborescence

```text
main.py                 flow Prefect : enchaîne les 4 fonctions mères
src/                    ingestion, data_validation (GX), preprocessing, training, evaluation, tracking (MLflow),
                        registry (registre, alias), qualification (politique), ledger (lots), pipeline_events
orchestration/          tâches Prefect, hooks, déploiement planifié, automation Teams
api_prediction/         API FastAPI (schémas, service, traçabilité)
configs/                config.json, qualification_policy.json, service_manifest.json
db/init-db.sql          schéma tracing (rôles api_writer / grafana_reader)
grafana/                provisioning + générateur du dashboard
load_tests/             scénarios Locust, campagne, analyse
docker/prefect/         image Prefect corrigée
tests/                  tests unitaires et de contrat
docs/                   protocole et résultats de charge, preuves
```
