# Variables des clients Python (le .env du Compose n'est pas lu par l'application).
# Usage dans PowerShell :  . .\scripts\env.ps1
$env:MLFLOW_TRACKING_URI = "http://localhost:7281"
$env:MLFLOW_REGISTRY_URI = "http://localhost:7281"
$env:PREFECT_API_URL = "http://localhost:4200/api"
$env:PIPELINE_DB_DSN = "postgresql://api_writer:5mlde-local-api@127.0.0.1:15432/bank_api"
$env:API_DB_DSN = "postgresql://api_writer:5mlde-local-api@127.0.0.1:15432/bank_api"
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONWARNINGS = "ignore"
