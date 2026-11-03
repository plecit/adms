@echo off
title ADMS Background Worker
cd /d "%~dp0\.."
echo ========================================================
echo  Menjalankan ADMS Background Worker (Dispatcher & Outbox)
echo ========================================================
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -m app.workers.runner
) else (
    python -m app.workers.runner
)
pause
