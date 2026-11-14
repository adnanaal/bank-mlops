# Guide de démonstration

Ce guide décrit le déroulé de la démonstration demandée par le sujet : un fonctionnement nominal de bout en
bout, puis deux incidents contrôlés (lot de données invalide et dépendance indisponible), et enfin un retour
à une version précédente. Durée indicative : 10 à 12 minutes.

Toutes les commandes sont à lancer depuis la racine du dépôt, dans PowerShell, après avoir chargé les variables
d'environnement (`. .\scripts\env.ps1`) dans chaque terminal.

## 1. Préparation (avant la séance)

| Étape | Commande ou action | Résultat attendu |
|---|---|---|
| Démarrer Docker Desktop | Attendre l'état « Engine running » | Moteur Docker disponible |
| Démarrer les services | `docker compose up -d --wait` | MLflow, Prefect, PostgreSQL et Grafana en état `healthy` |
| Vérifier le registre | `uv run --locked python scripts/select_model.py show` | Liste des versions et version portant l'alias `champion` |
| Construire le manifeste | `uv run --locked python scripts/build_manifest.py` | `configs/service_manifest.json` désigne la version champion |
| Terminal 2 : lancer l'API | `uv run --locked uvicorn api_prediction.main:app --host 127.0.0.1 --port 8001 --workers 2` | `/health/ready` renvoie 200 |
| Terminal 3 : lancer le déploiement planifié | `uv run --locked python orchestration/deploy.py --interval 60` | Déploiement `bank-batch-watcher/planifie` visible dans Prefect |
| Ouvrir les interfaces | MLflow http://127.0.0.1:7281, Prefect http://127.0.0.1:4200, Grafana http://127.0.0.1:3000, API http://127.0.0.1:8001/docs, canal Teams | Onglets prêts |

Avant la séance, vérifier qu'aucun lot n'est en attente dans `data/landing` qui n'ait pas déjà été traité
(le registre `data/state/ledger.json` indique les lots traités).

## 2. Fonctionnement nominal (environ 5 minutes)

1. **Arrivée d'un lot.** `uv run --locked python scripts/simulate_arrival.py`
   Le prochain lot mensuel du catalogue (par exemple `2009-11`) est déposé dans `data/landing`.
2. **Déclenchement automatique.** Dans Prefect, une exécution du flow `bank-training-pipeline` démarre au
   passage suivant du déploiement planifié. Montrer les quatre tâches, leur durée et leurs journaux.
3. **Validation.** Dans les journaux de la tâche d'ingestion, les quatre suites Great Expectations sont
   validées avant le prétraitement.
4. **Traçabilité.** Dans MLflow, ouvrir le nouveau run : paramètres, métriques, snapshot et son empreinte,
   rapports de validation, modèle. Dans le registre, la nouvelle version porte le tag de qualification.
5. **Décision de mise en service.** `uv run --locked python scripts/select_model.py show`
   La nouvelle version est soit sélectionnée (alias `champion` déplacé), soit qualifiée mais non retenue.
   Si elle est sélectionnée : reconstruire le manifeste (`scripts/build_manifest.py`) et redémarrer l'API.
6. **Utilisation du service.**
   `uv run --locked python scripts/demo_traffic.py --units 20 --batches 2 --feedback-share 0.4 --campaign demo-soutenance`
   Les réponses indiquent la version exacte du modèle. Montrer également une requête invalide dans `/docs`
   (réponse 422).
7. **Supervision.** Dans Grafana, actualiser le tableau de bord et filtrer sur la campagne `demo-soutenance` :
   requêtes, latences, prédictions par version, retours et nouvelle exécution du pipeline.

## 3. Incident contrôlé : lot de données invalide (environ 2 minutes)

1. `uv run --locked python scripts/make_corrupt_batch.py --period 2010-03`
   Un lot `2010-03-corrompu` contenant des âges négatifs, du texte dans une variable numérique, des
   identifiants dupliqués et 60 % de professions inconnues est déposé.
2. Résultats attendus :
   - l'exécution Prefect passe à l'état `Failed` à la fin de l'ingestion, sans prétraitement ;
   - un rapport de validation est écrit dans `data/diagnostics/validation` ;
   - le lot est inscrit comme `rejected` dans `data/state/ledger.json` et ne sera pas retraité ;
   - le run MLflow est fermé en échec, aucune nouvelle version n'est créée, le champion est inchangé ;
   - un message d'alerte arrive dans le canal Teams (`scripts/check_alerts.py --minutes 10` le confirme
     côté serveur).

## 4. Incident contrôlé : dépendance indisponible (environ 1 minute)

`uv run --locked python scripts/demo_dependency_outage.py`

Le script vérifie le fonctionnement nominal, arrête PostgreSQL, constate que `/health/ready` et `/predict`
renvoient 503 (aucune prédiction servie sans trace), redémarre PostgreSQL et vérifie la reprise sans
redémarrer l'API.

## 5. Retour à une version précédente (environ 1 minute)

1. `uv run --locked python scripts/select_model.py rollback --reason "démonstration du retour arrière"`
2. `uv run --locked python scripts/build_manifest.py`, puis redémarrer l'API.
3. `GET /model-info` indique la version précédente ; l'opération est tracée dans
   `data/state/selection_history.jsonl` (acteur, date, motif, ancienne et nouvelle version).
4. Pour revenir à l'état initial : `scripts/select_model.py select --version <n> --reason "fin de démonstration"`.

## 6. Solution de repli

Si un service ne répond pas pendant la séance, les preuves des mêmes scénarios, réalisés le 8 novembre 2026,
sont disponibles dans `docs/preuves/` et résumées dans la section 2 de `SYNTHESE_OPS.md` :

| Scénario | Preuve |
|---|---|
| Fonctionnement nominal | `partie3_serve.log`, `partie3_trafic_nominal.txt`, `partie4_totaux_demo_nominal.txt` |
| Lot invalide | `partie2_scenario1_lot_invalide.log` |
| Panne transitoire de MLflow | `partie2_scenario2_panne_transitoire.log` |
| Serveur MLflow arrêté et reprise | `partie2_scenario3_mlflow_arrete.log`, `partie2_scenario3_reprise.log` |
| PostgreSQL arrêté | `partie3_panne_postgres.txt` |
| Retour arrière | `partie3_rollback.txt`, `partie3_selection_refusee.txt` |
| Tableau de bord | `grafana_dashboard_24h.png`, `grafana_dashboard_demos.png`, `partie4_check_dashboard.txt` |

## 7. Après la démonstration

Arrêter le déploiement planifié (Ctrl+C dans le terminal 3) et l'API (Ctrl+C dans le terminal 2), puis
`docker compose stop`. Ne pas utiliser `docker compose down --volumes`, qui supprimerait l'historique MLflow,
Prefect, PostgreSQL et Grafana.
