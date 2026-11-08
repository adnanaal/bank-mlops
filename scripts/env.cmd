@echo off
REM Variables des clients Python (le .env du Compose n'est pas lu par l'application).
REM Usage dans cmd :  call scripts\env.cmd
set MLFLOW_TRACKING_URI=http://localhost:7281
set MLFLOW_REGISTRY_URI=http://localhost:7281
set PREFECT_API_URL=http://localhost:4200/api
set PIPELINE_DB_DSN=postgresql://api_writer:5mlde-local-api@127.0.0.1:15432/bank_api
set API_DB_DSN=postgresql://api_writer:5mlde-local-api@127.0.0.1:15432/bank_api
set PYTHONIOENCODING=utf-8
set PYTHONWARNINGS=ignore
