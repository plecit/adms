# Script Instalasi Windows Service menggunakan NSSM untuk Windows Server 2016
# Jalankan PowerShell sebagai Administrator!

param (
    [string]$AppDir = "$PSScriptRoot\..",
    [string]$NssmPath = "nssm.exe",
    [int]$Port = 80
)

$AppDir = (Resolve-Path $AppDir).Path
$PythonExe = "$AppDir\.venv\Scripts\python.exe"

if (-not (Test-Path $PythonExe)) {
    Write-Error "Virtual environment tidak ditemukan di $PythonExe. Jalankan 'python -m venv .venv' terlebih dahulu."
    exit 1
}

$LogsDir = "$AppDir\logs"
if (-not (Test-Path $LogsDir)) {
    New-Item -ItemType Directory -Path $LogsDir | Out-Null
}

Write-Host "Mendaftarkan ADMS-API sebagai Windows Service..." -ForegroundColor Cyan
& $NssmPath install ADMS-API $PythonExe "-m uvicorn app.main:app --host 0.0.0.0 --port $Port"
& $NssmPath set ADMS-API AppDirectory $AppDir
& $NssmPath set ADMS-API Description "ADMS Server ZKTeco HTTP & iClock Service"
& $NssmPath set ADMS-API Start SERVICE_AUTO_START
& $NssmPath set ADMS-API AppStdout "$LogsDir\api_stdout.log"
& $NssmPath set ADMS-API AppStderr "$LogsDir\api_stderr.log"

Write-Host "Mendaftarkan ADMS-Worker sebagai Windows Service..." -ForegroundColor Cyan
& $NssmPath install ADMS-Worker $PythonExe "-m app.workers.runner"
& $NssmPath set ADMS-Worker AppDirectory $AppDir
& $NssmPath set ADMS-Worker Description "ADMS Background Worker (Command Dispatcher & Outbox)"
& $NssmPath set ADMS-Worker Start SERVICE_AUTO_START
& $NssmPath set ADMS-Worker AppStdout "$LogsDir\worker_stdout.log"
& $NssmPath set ADMS-Worker AppStderr "$LogsDir\worker_stderr.log"

Write-Host "Menjalankan service..." -ForegroundColor Green
& $NssmPath start ADMS-API
& $NssmPath start ADMS-Worker

Write-Host "Selesai! Service ADMS-API dan ADMS-Worker telah aktif dan berjalan di background." -ForegroundColor Green
