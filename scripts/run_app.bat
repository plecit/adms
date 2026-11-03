@echo off
title ADMS Web & iClock Server
cd /d "%~dp0\.."
echo ========================================================
echo  Menjalankan ADMS Server (FastAPI + Uvicorn)
echo ========================================================
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -m uvicorn app.main:app --host 0.0.0.0 --port 80
) else (
    python -m uvicorn app.main:app --host 0.0.0.0 --port 80
)
pause
