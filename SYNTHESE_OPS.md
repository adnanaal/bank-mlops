# Synthèse des choix et résultats OPS

Projet seul, exécution locale (Windows 11, 8 cœurs logiques, 32 Go, Docker Desktop/WSL 2). Les outils et
versions sont ceux des TP 5MLDE pour rester comparables au cours ; chaque écart est justifié ci-dessous.

## 1. Décisions techniques

| Sujet | Décision | Justification |
|---|---|---|
| Données | `bank-additional-full.csv` (UCI, CC BY 4.0), téléchargé par script avec SHA-256 | Version la plus proche de Moro et al. (2014) ; aucune donnée dans Git |
| Arrivée des données | Rejeu déclaré : corpus initial 2008, puis 19 lots mensuels déposés dans `data/landing` | Les lignes sont ordonnées par date : le rejeu respecte l'ordre réel |
| Fuite | `duration` exclue | Connue seulement après l'appel (avertissement UCI) |
| Protocole de comparaison | Corpus de qualification fixe (~15 % de chaque mois, hachage stable de `client_id`), jamais entraîné | Toutes les versions sont comparées sur le même corpus (empreinte vérifiée) |
| Modèle | `Pipeline` sklearn : pdays=999 → indicateur, imputation, standardisation, one-hot (catégories rares/inconnues), régression logistique équilibrée | Un seul objet sérialisé pour l'entraînement et l'API : pas d'écart de prétraitement |
| Contrôles qualité | Great Expectations 1.23.2, 4 suites (lot strict, qualité du lot, snapshot, aptitude), appelées à la fin de `load_data` | Toute suite en échec, erreur GX ou résultat absent lève une exception : aucun prétraitement |
| Lots rejetés | Inscrits `rejected` dans un registre durable (nom + empreinte) | Jamais rejoués tels quels ; une version corrigée (autre empreinte) reste traitable et traçable |
| Traçabilité | MLflow : un run par tentative, run_id explicite, snapshot + split + rapports GX en artefacts, `log_input` | Retrouver données, code (commit), configuration et résultats sans le terminal |
| Sérialisation | `cloudpickle` + `code_paths=src` | Le pipeline contient une fonction maison ; rechargement vérifié à 1e-9 près |
| Mise en service | Politique `bank-qualif-v1` versionnée ; tags `production-ready` / `rejected` ; alias `champion` si non-infériorité (marge 0,005 d'AUC) | Qualification (seuils absolus) et remplacement du champion (comparaison) sont deux décisions distinctes |
| Retour arrière | `scripts/select_model.py rollback` + historique JSONL ; manifeste de service archivé | Un alias mutable n'identifie pas ce qui a prédit : l'API sert une version précise |
| Orchestration | Prefect 3.8.7 : flow + 4 tâches (0 retry), seule la publication MLflow est réessayée (2 retries sur erreur transitoire, idempotente par run_id) | Une non-conformité métier ne se corrige pas par un retry ; une publication doit éviter les doublons |
| Déclenchement automatique | Déploiement `flow.serve` planifié qui traite le prochain lot en attente | Pas de worker supplémentaire ; le serveur Prefect observe, le processus local exécute |
| Alertes | Automation Prefect (SDK) sur `Failed`/`Crashed` du flow d'entraînement → block Teams → Workflows | Côté serveur : fonctionne même si le processus du flow a disparu ; pas d'alerte sur les retries |
| API | FastAPI, handlers synchrones, modèle chargé une fois par worker, pool psycopg (5 connexions/worker), 1 thread numérique | Pas de blocage de la boucle async ; parallélisme maîtrisé |
| Persistance | PostgreSQL, schéma `tracing` dérivé du TP5 + `pipeline_events` ; succès HTTP seulement après commit | Pas de prédiction partielle ni de faux succès |
| Supervision | Grafana provisionné sur PostgreSQL (sans Prometheus) | Le socle SQL suffit (TP5) ; une seule source de vérité pour usage, erreurs, versions et traitements |

### Écarts et incidents d'intégration rencontrés

- **Prefect 3.8.7 en Docker** : l'image officielle embarque SQLAlchemy 2.1.1 ; le planificateur du serveur échouait
  à chaque passage (`Can't evaluate bulk DML statement`) et aucune exécution planifiée n'était créée. Correctif :
  image dérivée (`docker/prefect/Dockerfile`) avec SQLAlchemy 2.0.57, même version de Prefect.
- **FastAPI** : la version épinglée du TP2 (0.115.12) est incompatible avec Prefect 3.8.7 (starlette ≥ 1.0) ;
  FastAPI et Uvicorn sont laissés à la résolution d'uv, verrouillée dans `uv.lock`.
- **Ports Windows** : 5432/5433 réservés par le système (WinError 10013) → PostgreSQL publié sur 15432.
- **Dérive temporelle** : le taux de souscription passe de 3 % (mai 2008) à plus de 50 % (2010). Le modèle
  entraîné sur 2008 seul obtient une AUC de qualification de 0,36 : la politique le rejette. Il devient qualifiable
  dès l'intégration des premiers lots de 2009 (voir 2).

## 2. Preuves d'intégration

Journaux complets dans `docs/preuves/` ; événements serveur Prefect dans `partie2_evenements_prefect.txt`.

### Alerting et reprises (8 novembre 2026)

| Scénario | Flow run | Comportement observé | Alerte Teams |
|---|---|---|---|
| Lot corrompu (`2009-08-corrompu`, `make_corrupt_batch.py`) | `humorous-moth` | GX rejette 5 règles (âges négatifs, texte dans `campaign`, `client_id` dupliqués, 60 % de `job=unknown`) avant tout prétraitement ; lot inscrit `rejected` ; run MLflow fermé en FAILED par le hook ; flow `Failed` | Oui : automation déclenchée et action exécutée 2 s après l'échec |
| Panne transitoire MLflow pendant la publication (2 échecs injectés, `BANK_FAULT_PUBLISH_FAILURES=2`) | `quirky-dachshund` | Tâche `publication-mlflow` : retry 1/2 puis 2/2 à 5 s, succès à la 3e tentative ; version 8 publiée une seule fois, qualifiée et sélectionnée ; flow `Completed` | Non (silencieux, comme attendu) |
| Serveur MLflow arrêté (`docker compose stop mlflow`) | `military-groundhog` | Échec explicite en quelques secondes dès l'ouverture du run (`Connection refused`), aucun entraînement ni publication ; lot `2009-09` laissé en attente (échec technique, pas un rejet métier) | Oui |
| Reprise après redémarrage de MLflow | `gifted-elephant` | Le même lot `2009-09` est retraité automatiquement : version 9 publiée et sélectionnée | Non |

### Démonstrations de service (8 novembre 2026)

| Démo | Déroulé et résultat | Preuve |
|---|---|---|
| Nominal de bout en bout | Lot `2009-10` déposé → détecté par le déploiement planifié (`responsible-deer`) → v10 publiée, **qualifiée mais non sélectionnée** (AUC 0,719 < champion v9 0,791 − 0,005) → manifeste résolu sur v9 → API 2 workers → 160 prédictions (60 unitaires + 5 lots de 20), 77 feedbacks avec labels historiques, 1 requête invalide refusée (422) | `partie3_serve.log`, `partie3_trafic_nominal.txt` |
| PostgreSQL arrêté | `/health/ready` et `/predict` → 503 `storage_unavailable` pendant la panne, diagnostic local écrit, aucune prédiction servie sans trace ; reprise automatique au redémarrage, sans relancer l'API | `partie3_panne_postgres.txt` |
| Retour arrière | `select_model.py rollback` : champion v9 → v8 (acteur, date, motif tracés) ; tentative de sélection de la v2 refusée (`qualification = 'rejected'`) ; nouveau manifeste, API redémarrée sur v8 | `partie3_rollback.txt`, `partie3_selection_refusee.txt` |
| Traces par version | En base : campagne `demo-nominal` → 160 prédictions v9 ; `demo-apres-rollback` → 40 prédictions v8 ; les anciennes traces ne sont pas réécrites | jointure `tracing.predictions` → `tracing.model_versions` par campagne |

Historique complet du registre (10 versions) : v1 `pending` (publication interrompue par un bug corrigé, run
FAILED, identifiable), v2 `rejected` (corpus 2008 seul, dérive), v3→v9 qualifiées, v5 et v10 qualifiées mais non
sélectionnées (inférieures au champion), champion actuel v8 après retour arrière.

### Supervision (Grafana)

Dashboard « Bank MLOps — usage, performances et traitements » provisionné depuis `grafana/dashboards/bank-api.json`
(généré par `grafana/build_dashboard.py`, requêtes SQL versionnées), retrouvé après redémarrage de Grafana.
18 panneaux en 4 sections : usage (requêtes, req/s, clients/s), erreurs et durées (4xx/5xx par code, p50/p95/p99
calculés sur les requêtes, inférence séparée, taille des lots, activité par worker), modèles et retours
(prédictions par version exacte, couverture des feedbacks, qualité observée hors labels artificiels),
traitements Prefect (`pipeline_events` : démarrages, succès, échecs, lot, version, qualification).

- `grafana/check_dashboard.py` exécute chaque requête via l'API Grafana : 18/18 OK
  (`partie4_check_dashboard.txt`).
- Vérification manuelle des totaux sur une campagne connue (`partie4_totaux_demo_nominal.txt`) :
  `demo-nominal` = 60 `/predict` + 5 lots + 77 feedbacks + 1 requête invalide = **143 requêtes**, 160 clients
  scorés, identiques aux compteurs du script de trafic.
- Captures : `grafana_dashboard_24h.png` (inclut les tirs de charge), `grafana_dashboard_demos.png`
  (campagnes de démonstration, bascule v9 → v8 visible).
- Défauts corrigés grâce aux captures : panneau texte « version servie » vide, lignes tracées à travers les
  périodes sans activité, traces des tests automatisés (`pytest-*`) mêlées aux campagnes.

Les rejets métier ne sont jamais réessayés ; seule la publication MLflow (effet externe idempotent par `run_id`)
bénéficie de retries ; un échec technique laisse le lot en attente pour une reprise.

## 3. Tests de charge et dimensionnement

Protocole et objectifs fixés avant les mesures : `docs/charge/protocole.md` (p95 client < 300 ms pour
`/predict`, < 1 000 ms pour un lot de 20, < 1 % d'échecs). Détail : `docs/charge/resultats.md` et graphiques.
45 tirs de 60 s, modèle v7 figé, traçabilité PostgreSQL active, **0 % d'échec sur l'ensemble des tirs**.

### Palier critique répété (prédiction unitaire)

| Workers | Utilisateurs | Tirs | Débit médian (min) | p95 médian (min – max) | Tirs conformes | CPU API | CPU poste | RAM API |
|---|---|---|---|---|---|---|---|---|
| 2 | 20 | 4 | 95,5 req/s (91,8) | 160 ms (140 – 190) | 4/4 | 1,5 cœur | 42 % | 472 Mo |
| 2 | 30 | 3 | 90,1 req/s (88,2) | 460 ms (290 – 490) | 1/3 | 1,6 cœur | 58 % | 476 Mo |
| 2 | 40 | 4 | 105,7 req/s (83,4) | 420 ms (330 – 590) | 0/4 | 1,8 cœur | 49 % | 478 Mo |
| 4 | 20 | 4 | 95,8 req/s (70,7) | 175 ms (100 – 410) | 3/4 | 1,4 cœur | 37 % | 905 Mo |
| 4 | 30 | 3 | 48,3 req/s (48,1) | 1 200 ms (580 – 1 400) | 0/3 | 1,5 cœur | 87 % | 912 Mo |

Autres configurations (1 tir) : 1 worker tient l'objectif jusqu'à 10 utilisateurs (39 req/s, p95 290 ms) ;
les lots de 20 respectent l'objectif à tous les paliers testés, jusqu'à ~1 860 clients/s avec 2 workers
(borne inférieure : aucun palier dégradé).

### Recommandation chiffrée

- **2 workers Uvicorn**, 1 thread numérique chacun, pool PostgreSQL de 5 connexions par worker (10 au total).
- **Capacité soutenable mesurée : ~90 req/s en prédiction unitaire** (minimum des 4 tirs conformes : 91,8 req/s,
  p95 ≤ 190 ms) ; point de saturation entre 20 et 30 utilisateurs concurrents. Pour un trafic par lots :
  au moins 1 800 clients scorés/s (borne inférieure).
- **Ressources à réserver** : 2 cœurs CPU (1,5 cœur consommé au palier retenu) et 1 Go de RAM pour l'API
  (~240 Mo par worker + marge), hors PostgreSQL/MLflow. Exploiter à ~70 % de la capacité (≈ 65 req/s) pour
  absorber les pics ; au-delà, ajouter une instance plutôt que des workers sur la même machine.
- **4 workers non recommandés sur ce poste** : débit identique à 20 utilisateurs, mémoire doublée (905 Mo), et
  dégradation forte au-delà (p95 1,2 s) car le poste sature (CPU 87 à 93 %) : Locust, Docker et 4 workers se
  disputent 8 cœurs logiques. Ce résultat dépend de la machine et ne prouve pas l'inutilité de 4 workers sur
  un serveur dédié.
- Pour un usage majoritairement par lots, privilégier `/predict/batch` : 20 fois plus de clients par requête
  pour un p95 comparable.

## 4. Limites connues

- Labels du rejeu historiques : les feedbacks de charge sont artificiels (`label_source=loadtest`) et exclus des
  indicateurs de qualité réelle.
- Le corpus de qualification couvre toute la période (y compris des mois postérieurs à l'entraînement) : il sert
  à sélectionner, pas à estimer une performance future non biaisée.
- Un poste unique héberge Locust, Docker et l'API : capacité non transposable telle quelle à un serveur.
- Écritures MLflow (artefacts, version, tags) non transactionnelles : une publication partielle reste identifiable
  (tag `pending`) et la reprise retrouve la version par son run_id.
- Une panne PostgreSQL crée un trou dans le dashboard (journal local `data/state/api_trace_failures.jsonl`).
- Sécurité : services liés à 127.0.0.1, mots de passe de démonstration ; pas d'authentification de l'API.
- Éléments des TP non repris car non exigés par le sujet : API de collecte PUSH (TP2 ; le déclenchement
  automatique passe par le déploiement Prefect planifié) et expérience de cache Prefect (TP4).
- Le planning de démonstration (60 s) a rattrapé des exécutions manquées après l'arrêt du poste : sans lot en
  attente, ces passages se terminent immédiatement en succès.
