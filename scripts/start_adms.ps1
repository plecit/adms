# PowerShell Launcher untuk ADMS Server di Windows Server 2016
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Resolve-Path "$ScriptDir\.."
Set-Location $ProjectRoot

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "  MEMULAI ADMS SERVER UNTUK ZKTECO (WINDOWS SERVER 2016)   " -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

$PythonExe = "$ProjectRoot\.venv\Scripts\python.exe"
if (-not (Test-Path $PythonExe)) {
    $PythonExe = "python.exe"
}

# Jalankan Inisialisasi Database
Write-Host "[1/3] Memeriksa dan menginisialisasi skema database..." -ForegroundColor Yellow
& $PythonExe -m app.db.init_db

# Buat folder logs jika belum ada
$LogsDir = "$ProjectRoot\logs"
if (-not (Test-Path $LogsDir)) {
    New-Item -ItemType Directory -Path $LogsDir | Out-Null
}

Write-Host "[2/3] Memulai Background Worker..." -ForegroundColor Yellow
Start-Process -FilePath $PythonExe -ArgumentList "-m app.workers.runner" -WorkingDirectory $ProjectRoot

Write-Host "[3/3] Memulai Uvicorn HTTP Server pada port 80..." -ForegroundColor Green
Write-Host "Dashboard web dapat diakses di http://localhost/ atau IP server ini" -ForegroundColor White
& $PythonExe -m uvicorn app.main:app --host 0.0.0.0 --port 80
