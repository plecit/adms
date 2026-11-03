# ADMS Server ZKTeco (SenseFace 4A + M2-LR)

Aplikasi **Automatic Data Master Server (ADMS)** mandiri berperforma tinggi untuk mesin absensi ZKTeco berbasis **Push Protocol (iClock)** via HTTP, dirancang khusus untuk deployment produksi di **Windows Server 2016** atau lingkungan kontainer (Docker).

---

## 🚀 Fitur Utama

1. **Ingest Log Absensi (ATTLOG) Anti-Redundan & Zero Data Loss**:
   - Protokol *at-least-once*: Mesin ZKTeco dapat mengirim ulang log kapan saja (mati listrik, jaringan putus, stamp reset) tanpa menyebabkan duplikasi data.
   - Menggunakan PostgreSQL `ON CONFLICT (device_sn, pin, punched_at) DO NOTHING` dan audit deteksi perbedaan nilai di `attlog_conflicts`.
   - Penyimpanan audit mentah `raw_requests` (append-only) sebelum parsing.
   - Respon `OK:<n>` **hanya dibalas setelah transaksi database sukses di-commit**.
   - Penanganan zona waktu deterministik (default UTC+7 WIB / 420 menit).

2. **Antrian Perintah Mesin (Device Commands Queue)**:
   - Polling periodik `GET /iclock/getrequest` menggunakan `SELECT ... FOR UPDATE SKIP LOCKED`.
   - Idempotency key per perintah, supersede versi lama, dan pelaporan eksekusi via `POST /iclock/devicecmd`.
   - Worker pemulihan timeout (reclaim ke `pending`) dan dead-letter queue.

3. **Registrasi Karyawan & Distribusi Biometrik**:
   - Tambah karyawan via Web UI atau REST API dan otomatis push `DATA UPDATE USERINFO` ke mesin tujuan.
   - Deteksi karyawan baru yang didaftarkan langsung di mesin fisik (`needs_review=True`).
   - Distribusi template biometrik (Wajah Visible Light `BIODATA Type=9` & Sidik Jari `FP`) dari mesin MASTER ke mesin target yang kompatibel tanpa loopback.
   - Enkripsi biometrik at-rest menggunakan **AES-256-GCM** (UU PDP No. 27/2022).

4. **Transactional Outbox ke Sistem Hilir**:
   - Meneruskan setiap punch absensi baru ke sistem HR / Payroll / Webhook eksternal dengan header `X-Idempotency-Key`.
   - Retry otomatis dengan *exponential backoff* dan *jitter*.

5. **Dashboard Web Admin (FastAPI + Jinja2)**:
   - Monitoring status mesin (Online/Offline, last seen, model, firmware, time skew).
   - Feed log absensi real-time dengan filter tanggal, PIN, dan jendela debounce.
   - Manajemen mesin, antrian command, dan audit raw requests.

6. **Simulator Mesin Lengkap (`tools/fake_device.py`)**:
   - Mensimulasikan SenseFace 4A dan M2-LR untuk pengujian handshake, upload batch ribuan data, uji duplikasi 10x, dan eksekusi command.

---

## 📁 Struktur Direktori

```
ADMS/
├── app/
│   ├── api/             # REST API: devices, employees, attlog, commands, stats
│   ├── db/              # Session SQLAlchemy async, Base, init_db
│   ├── iclock/          # Endpoint ZKTeco Push Protocol (cdata, getrequest, devicecmd)
│   ├── models/          # Skema database relasional SQLAlchemy 2.0
│   ├── parsers/         # Parser ATTLOG, OPERLOG, USER, FP, BIODATA, Options
│   ├── services/        # attlog_ingest, enrollment, distribution, command_queue, crypto
│   ├── web/             # Web Admin UI (Jinja2 templates & routes)
│   └── workers/         # Background worker: runner, command_dispatcher, outbox_forwarder, offline_monitor
├── docs/
│   ├── decisions.md                 # Architectural Decision Records (ADR)
│   ├── protocol_notes.md            # Catatan spesifikasi protokol iClock ZKTeco
│   ├── windows_server_deployment.md # Panduan lengkap Windows Server 2016
│   └── runbook.md                   # SOP operasional, penanganan insiden, & backup
├── scripts/
│   ├── install_windows_service.ps1  # Registrasi service NSSM
│   ├── start_adms.ps1               # Launcher PowerShell
│   ├── run_app.bat                  # Batch runner server HTTP
│   ├── run_worker.bat               # Batch runner background worker
│   └── backup_db.ps1                # Script backup otomatis PostgreSQL
├── tools/
│   └── fake_device.py               # Simulator mesin absensi ZKTeco
├── tests/                           # 28 test cases otomatis pytest
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
├── .env.example
└── project.md
```

---

## 💻 Panduan Cepat Menjalankan di Windows Server 2016

### 1. Prasyarat
- Windows Server 2016 x64
- Python 3.11 atau 3.12 (centang *"Add to PATH"*)
- PostgreSQL 16 (buat database `adms_db`)

### 2. Setup Lingkungan
```powershell
cd C:\ADMS
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```
*(Sesuaikan password database PostgreSQL pada file `.env`)*

### 3. Inisialisasi Database
```powershell
.\.venv\Scripts\python -m app.db.init_db
```

### 4. Menjalankan Aplikasi
- **Mode Uji Coba Langsung**:
  ```powershell
  .\scripts\start_adms.ps1
  ```
  Buka browser: `http://localhost/` untuk melihat Dashboard Web.

- **Mode Produksi (Windows Service via NSSM)**:
  Jalankan PowerShell sebagai Administrator:
  ```powershell
  .\scripts\install_windows_service.ps1
  ```
  Service `ADMS-API` dan `ADMS-Worker` akan otomatis menyala saat Windows Server reboot.

---

## 🧪 Menjalankan Pengujian Otomatis (Pytest)

Aplikasi dilengkapi 28 unit & integration test yang menguji seluruh skenario kegagalan:
```powershell
.\.venv\Scripts\pytest -v
```
Semua test menguji:
- Duplikasi batch 10x -> nol duplikat
- Reset Stamp & upload ulang riwayat -> nol baris baru
- Deteksi konflik status -> tercatat di `attlog_conflicts`
- Baris corrupt tidak menggagalkan baris valid
- Eksekusi command, polling, ACK, dan timeout recovery
- Enkripsi AES-256-GCM biometrik
- Fan-out template master ke target yang kompatibel

---

## 📟 Pengaturan pada Mesin ZKTeco Fisik
1. Masuk ke **Menu Mesin > Comm. > Cloud Server Setting** (atau **ADMS / Webserver Setting**).
2. Masukkan:
   - **Server Address**: IP Windows Server Anda (misal `192.168.1.50`).
   - **Server Port**: `80` (atau port yang Anda atur).
   - **Enable Domain Name**: Nonaktifkan.
3. Mesin akan segera melakukan handshake dan muncul dalam daftar **Online** di dashboard.
