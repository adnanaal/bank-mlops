# Image Prefect du cours (3.8.7) avec SQLAlchemy figé en 2.0.x.
# Constat : l'image officielle embarque SQLAlchemy 2.1.1 ; le planificateur du serveur échoue alors à chaque
# passage (« Can't evaluate bulk DML statement ») et aucune exécution planifiée n'est créée.
FROM prefecthq/prefect:3.8.7-python3.12
RUN pip install --no-cache-dir "sqlalchemy[asyncio]>=2.0.36,<2.1"
