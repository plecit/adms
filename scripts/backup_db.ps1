# Script Backup Otomatis Database PostgreSQL untuk Windows Server
param (
    [string]$DbName = "adms_db",
    [string]$DbUser = "postgres",
    [string]$DbHost = "127.0.0.1",
    [int]$DbPort = 5432,
    [string]$BackupDir = "$PSScriptRoot\..\backups",
    [int]$RetentionDays = 30
)

if (-not (Test-Path $BackupDir)) {
    New-Item -ItemType Directory -Path $BackupDir | Out-Null
}

$Timestamp = (Get-Date).ToString("yyyyMMdd_HHmmss")
$BackupFile = "$BackupDir\${DbName}_${Timestamp}.sql"

Write-Host "Memulai backup database $DbName ke $BackupFile..." -ForegroundColor Cyan

# Jalankan pg_dump (pastikan folder bin PostgreSQL ada di PATH)
pg_dump -h $DbHost -p $DbPort -U $DbUser -F p -b -v -f $BackupFile $DbName

if ($LASTEXITCODE -eq 0) {
    Write-Host "Backup berhasil dibuat: $BackupFile" -ForegroundColor Green
    
    # Hapus file backup yang lebih tua dari RetentionDays
    Get-ChildItem -Path $BackupDir -Filter "*.sql" | Where-Object {
        $_.LastWriteTime -lt (Get-Date).AddDays(-$RetentionDays)
    } | Remove-Item -Force
    Write-Host "Pembersihan backup lama selesai." -ForegroundColor Gray
} else {
    Write-Error "Backup database gagal dengan exit code $LASTEXITCODE"
}
