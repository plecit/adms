# Architectural Decision Records (ADR) - ADMS Server

## ADR 001: Pilihan Stack & Database
- **Status:** Diterima
- **Konteks:** ADMS membutuhkan sistem ingest data absensi real-time dari puluhan/ratusan mesin ZKTeco yang tahan terhadap redudansi (jaringan putus-nyambung, pemadaman listrik mesin, re-upload stamp).
- **Keputusan:**
  1. **Python 3.12+ dengan FastAPI & Uvicorn**: Async I/O tinggi untuk melayani HTTP requests non-blocking dari perangkat iClock.
  2. **PostgreSQL 16**: Menggunakan fitur ACID transaction, `INSERT ... ON CONFLICT (device_sn, pin, punched_at) DO NOTHING`, dan transactional outbox pattern.
  3. **SQLAlchemy 2.0 (Async) + Alembic**: Pemodelan relasional strongly typed dan migrasi skema terstruktur.
  4. **Worker Terpisah Tanpa Redis/Celery**: Antrian command dan outbox dikelola langsung via tabel PostgreSQL (`FOR UPDATE SKIP LOCKED`). Ini memastikan state command dan event masuk ke transaksi DB yang sama dengan data bisnis (Transactional Outbox), tanpa risiko desinkronisasi antara Redis dan database.

## ADR 002: Desain Anti-Redudansi ATTLOG (Zero Data Loss & At-Least-Once Protocol)
- **Status:** Diterima
- **Konteks:** Protokol ZKTeco iClock bersifat *at-least-once*. Mesin akan terus mengirim ulang log jika tidak menerima respon `OK` valid dengan format `OK:<jumlah_baris>`.
- **Keputusan:**
  1. **Raw Log Ingest First**: Payload mentah dari setiap POST `/iclock/cdata` selalu disimpan ke tabel `raw_requests` (append-only) sebelum parsing dilakukan.
  2. **Unique Composite Constraint**: Tabel `attlog` memiliki unique constraint pada `(device_sn, pin, punched_at)`.
  3. **Conflict Inspection**: Jika terjadi duplikat tapi data atribut (status, verify_type, workcode) berbeda, baris disimpan di `attlog_conflicts` untuk audit manual tanpa menimpa data yang sudah ada.
  4. **Strict Response Timing**: Server HANYA merespon `OK:<n>` SETELAH transaksi database berhasil di-commit. Jika terjadi error database, server merespon HTTP 500 sehingga mesin ZKTeco menjadwalkan pengiriman ulang.
  5. **Timezone Deterministic Conversion**: Mesin mengirim waktu lokal tanpa timezone (`punched_at_local`). Server mengonversi ke `punched_at` (UTC timestamp) berdasarkan `tz_offset_minutes` mesin yang dicatat saat penerimaan (`tz_offset_at_receive`).

## ADR 003: Polling Command Queue & Idempotency
- **Status:** Diterima
- **Konteks:** Server tidak dapat melakukan push langsung ke mesin yang berada di balik NAT/firewall pelanggan. Mesin melakukan HTTP GET `/iclock/getrequest` secara periodik.
- **Keputusan:**
  1. Polling mengambil command `pending` menggunakan `SELECT ... FOR UPDATE SKIP LOCKED` untuk mencegah race condition antar request polling paralel.
  2. Setiap command memiliki `idempotency_key` (misal: `user:101:v1`, `biotmp:101:face:v2`).
  3. Command lama yang berstatus `pending` untuk entity yang sama akan di-supersede jika versi yang lebih baru dibuat.
  4. Worker `command_dispatcher` memonitor command berstatus `sent` yang tidak di-ack dalam batas waktu (timeout 5 menit) dan mengembalikannya ke `pending` untuk dikirim ulang.

## ADR 004: Keamanan Biometrik & Privasi (UU PDP No. 27/2022)
- **Status:** Diterima
- **Konteks:** Template wajah dan sidik jari adalah data biometrik pribadi yang sensitif dan wajib dilindungi.
- **Keputusan:**
  1. Template biometrik dienkripsi at-rest menggunakan algoritma AES-256-GCM dengan kunci simetris yang dibaca dari variabel environment (`BIOMETRIC_ENCRYPTION_KEY`).
  2. Isi mentah template (`TMP=`) tidak pernah dicetak ke log aplikasi (stdout/stderr).
  3. Hanya serial number (SN) yang terdaftar atau disetujui admin yang diizinkan untuk sinkronisasi. SN tidak dikenal disimpan di `raw_requests` dengan status ditolak/menunggu persetujuan.

## ADR 005: Kompatibilitas Windows Server 2016
- **Status:** Diterima
- **Konteks:** Aplikasi harus dapat beroperasi secara andal di lingkungan Windows Server 2016.
- **Keputusan:**
  1. Arsitektur backend mendukung eksekusi native di Windows via Python standard runtime dan PostgreSQL Windows.
  2. Disediakan manajemen service menggunakan **NSSM (Non-Sucking Service Manager)** atau script PowerShell Task Scheduler untuk auto-restart saat reboot dan recovery saat crash.
  3. Dukungan Reverse Proxy via Caddy for Windows (single binary, HTTPS otomatis/HTTP proxy) atau Nginx for Windows.
  4. Docker Compose tetap disediakan untuk deployment berbasis kontainer.
