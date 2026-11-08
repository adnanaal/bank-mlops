# Protocole des tests de charge (fixé avant les mesures)

## Objectifs de service (critères d'expérience pédagogiques, pas une promesse)

| Endpoint | p95 client | Taux d'échec | Palier retenu si… |
|---|---|---|---|
| `POST /predict` (1 client) | < 300 ms | < 1 % | les deux critères tiennent sur tout le tir de 60 s |
| `POST /predict/batch` (20 clients) | < 1 000 ms | < 1 % | idem |

La **capacité soutenable** d'une configuration est le plus grand débit observé (req/s et clients/s) d'un palier
qui respecte ces deux critères, pas le pic de RPS. Si aucun palier ne dégrade les critères, la capacité est
présentée comme une borne inférieure.

## Variables fixées

- Modèle : version précise du manifeste `configs/service_manifest.json` (champion résolu une fois).
- Données client : 500 clients du corpus de qualification, construits à l'avance (`data/loadtest/clients.json`).
- Temps d'attente Locust : 50 à 200 ms entre deux requêtes d'un utilisateur virtuel.
- Taille de lot : 20 clients. Parallélisme numérique : 1 thread par worker (`OMP_NUM_THREADS=1`).
- Pool PostgreSQL : 5 connexions maximum par worker. Traçabilité PostgreSQL **activée** (service complet mesuré).
- Durée : 60 s par tir, montée instantanée (`-r = -u`), chauffe de 15 s non mesurée après chaque démarrage.
- Charges parasites arrêtées : API manuelle, planificateur Prefect ; Grafana non consulté pendant la série.

## Variables étudiées

- Workers Uvicorn : 1, 2, 4 (serveur arrêté et redémarré entre deux configurations).
- Utilisateurs virtuels : 5, 10, 20, 40.
- Scénarios : unitaire, lot de 20, mixte (70 % unitaire, 20 % lot, 10 % feedback) à 20 utilisateurs.
- Répétitions : 3 tirs supplémentaires au palier critique et au palier suivant pour la configuration recommandée.

## Mesures conservées

CSV Locust par tir (`load_tests/resultats/<campagne>_stats.csv`), index des tirs (`index.jsonl`) avec CPU et RAM
des processus de l'API et du poste échantillonnés chaque seconde, identifiant de campagne transmis dans
`X-Test-Campaign` et retrouvé dans `tracing.api_requests`.

## Limites connues

Locust, Docker (PostgreSQL, MLflow, Prefect, Grafana) et l'API partagent le même poste (8 cœurs logiques, 32 Go) :
les résultats ne se généralisent pas à un serveur dédié. Les utilisateurs virtuels ne sont pas des requêtes/s.
