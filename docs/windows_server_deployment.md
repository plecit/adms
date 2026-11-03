# Panduan Deployment ADMS di Windows Server 2016

Dokumen ini adalah panduan langkah-demi-langkah instalasi dan pengoperasian ADMS Server pada sistem operasi **Windows Server 2016 (Standard / Datacenter)**.

---

## 1. Prasyarat Sistem (Prerequisites)

1. **Sistem Operasi**: Windows Server 2016 (x64) dengan update terbaru.
2. **Python**: Versi 3.11 atau 3.12 (x64)
   - Download installer Windows x64 dari [python.org](https://www.python.org/downloads/).
   - **PENTING saat instalasi**: Centang *"Add python.exe to PATH"* dan centang *"Install for all users"*.
3. **Database**: PostgreSQL 16 (x64)
   - Download PostgreSQL 16 Windows Installer dari EnterpriseDB (EDB).
   - Catat port (default: `5432`), user (default: `postgres`), dan password yang dibuat saat instalasi.
4. **NSSM (Non-Sucking Service Manager)**:
   - Tool untuk mendaftarkan FastAPI dan Background Worker sebagai **Windows Service** resmi yang otomatis menyala saat server reboot dan auto-restart jika terjadi kegagalan.
   - Download `nssm-2.24.zip` dari [nssm.cc](https://nssm.cc/download), ekstrak file `win64\nssm.exe` ke folder `C:\Windows\System32\` atau folder proyek `tools\nssm.exe`.
5. **Reverse Proxy (Opsional tapi Direkomendasikan)**:
   - **Caddy for Windows** (single binary executable) atau **Nginx for Windows** atau IIS. Caddy sangat direkomendasikan karena ringan dan mendukung auto-reconnect.

---

## 2. Pengaturan Database PostgreSQL di Windows Server

1. Buka **pgAdmin** atau **SQL Shell (psql)** pada Windows Server.
2. Buat database baru bernama `adms_db`:
   ```sql
   CREATE DATABASE adms_db WITH ENCODING 'UTF8';
   ```
3. Pastikan service `postgresql-x64-16` berstatus Running di `services.msc` dan Startup type = `Automatic`.

---

## 3. Instalasi & Konfigurasi ADMS

1. Clone atau letakkan folder proyek ke direktori server, misalnya:
   `C:\ADMS`

2. Buka PowerShell sebagai **Administrator** di folder `C:\ADMS`:
   ```powershell
   cd C:\ADMS
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   pip install --upgrade pip
   pip install -r requirements.txt
   ```

3. Buat file `.env` dari `.env.example`:
   ```powershell
   Copy-Item .env.example .env
   ```
   Edit file `.env` menggunakan Notepad:
   ```ini
   PORT=80
   HOST=0.0.0.0
   DATABASE_URL=postgresql+asyncpg://postgres:PasswordAnda@127.0.0.1:5432/adms_db
   DATABASE_URL_SYNC=postgresql://postgres:PasswordAnda@127.0.0.1:5432/adms_db
   SECRET_KEY=ganti-dengan-kunci-acak-panjang-rahasia-anda
   BIOMETRIC_ENCRYPTION_KEY=ganti-dengan-32-karakter-aes-key-rahasia
   DEFAULT_TZ_OFFSET_MINUTES=420
   ALLOW_UNKNOWN_DEVICES=true
   LOG_LEVEL=INFO
   ```

4. Jalankan inisialisasi tabel database:
   ```powershell
   .\.venv\Scripts\python -m app.db.init_db
   ```

---

## 4. Konfigurasi Windows Firewall

Mesin absensi ZKTeco perlu menghubungi port server (default port `80` atau port custom `8080`):
Jalankan perintah PowerShell berikut untuk membuka port:
```powershell
New-NetFirewallRule -DisplayName "ADMS Server HTTP Port 80" -Direction Inbound -LocalPort 80 -Protocol TCP -Action Allow
```
*(Ganti port 80 jika Anda menggunakan port lain).*

---

## 5. Menjalankan Sebagai Windows Service (Production)

Agar aplikasi otomatis berjalan saat Windows Server menyala (tanpa perlu user login), gunakan **NSSM**:

### 5.1 Service 1: ADMS Web & iClock Server (`ADMS-API`)
Jalankan PowerShell sebagai Administrator:
```powershell
# Daftarkan service API
nssm install ADMS-API "C:\ADMS\.venv\Scripts\python.exe" "-m uvicorn app.main:app --host 0.0.0.0 --port 80"
nssm set ADMS-API AppDirectory "C:\ADMS"
nssm set ADMS-API Description "ADMS Server for ZKTeco Attendance Machines (FastAPI & iClock Push Protocol)"
nssm set ADMS-API Start SERVICE_AUTO_START
nssm set ADMS-API AppStdout "C:\ADMS\logs\api_stdout.log"
nssm set ADMS-API AppStderr "C:\ADMS\logs\api_stderr.log"

# Jalankan service
nssm start ADMS-API
```

### 5.2 Service 2: ADMS Background Worker (`ADMS-Worker`)
Worker bertugas menangani timeout retry antrian command, pengiriman outbox ke payroll/HR, dan monitoring status offline:
```powershell
# Daftarkan service Worker
nssm install ADMS-Worker "C:\ADMS\.venv\Scripts\python.exe" "-m app.workers.runner"
nssm set ADMS-Worker AppDirectory "C:\ADMS"
nssm set ADMS-Worker Description "ADMS Background Worker for Command Dispatch, Outbox, and Device Monitoring"
nssm set ADMS-Worker Start SERVICE_AUTO_START
nssm set ADMS-Worker AppStdout "C:\ADMS\logs\worker_stdout.log"
nssm set ADMS-Worker AppStderr "C:\ADMS\logs\worker_stderr.log"

# Jalankan service
nssm start ADMS-Worker
```

### 5.3 Memeriksa Status Service
Ketik `services.msc` pada Run dialog Windows, cari `ADMS-API` dan `ADMS-Worker`. Pastikan keduanya berstatus `Running`.

---

## 6. Konfigurasi pada Mesin ZKTeco (SenseFace 4A & M2-LR)

1. Pada mesin fisik, masuk ke menu **Menu Utama** -> **Comm.** (Komunikasi) -> **Cloud Server Setting** (atau **ADMS / Webserver Setting**).
2. Isi parameter berikut:
   - **Server Address**: Masukkan IP Windows Server Anda (misal: `192.168.1.50`).
   - **Server Port**: Port yang dikonfigurasi (misal: `80` atau `8080`).
   - **Enable Domain Name**: Nonaktifkan (kecuali menggunakan FQDN DNS).
   - **Enable Proxy Server**: Nonaktifkan (OFF).
3. Simpan dan restart mesin atau tunggu 30 detik.
4. Buka dashboard web ADMS di browser server: `http://localhost/` untuk melihat mesin muncul dalam status Online!

---

## 7. Pemeliharaan & Backup Rutin

Disediakan script PowerShell otomatis `scripts/backup_db.ps1` yang dapat dijadwalkan via Windows **Task Scheduler** setiap tengah malam untuk mem-backup database PostgreSQL dan merotasi file backup lama.
