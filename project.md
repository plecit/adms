# PROJECT: ADMS Server untuk Mesin Absensi ZKTeco (SenseFace 4A + M2-LR)

> Dokumen ini adalah brief lengkap untuk agent (Hermes). Baca seluruhnya sebelum menulis kode.
> Bahasa dokumentasi & komentar kode: Indonesia. Nama variabel, tabel, endpoint: Inggris.

---

## 1. Tujuan

Membangun server **ADMS (Automatic Data Master Server)** sendiri, mirip fungsi inti BioTime / ZKBio, yang berbicara dengan mesin ZKTeco lewat **Push Protocol (iClock)** via HTTP.

Fitur wajib:

1. **Terima attendance log (ATTLOG)** dari semua mesin secara real-time, **tanpa duplikasi dan tanpa kehilangan data**, walaupun mesin mati/listrik drop atau jaringan putus-nyambung.
2. **Registrasi karyawan baru** dari web/API, otomatis dikirim ke semua mesin yang dituju.
3. **Distribusi template biometrik (fingerprint + face)** dari **mesin master** ke mesin lain.
4. Monitoring status mesin (online/offline, backlog, last seen).
5. **Antrian pengiriman** (ke mesin dan ke sistem hilir seperti HR/payroll) yang tahan gagal dan idempotent.

Di luar scope (tahap awal): payroll, shift/roster kompleks, laporan kehadiran lengkap, aplikasi mobile.

## 2. Perangkat

| Mesin | Catatan |
|---|---|
| ZKTeco **SenseFace 4A** | Face (visible light) + kemungkinan kartu/password. Umumnya push protocol 2.4.x dan data biometrik lewat `BIODATA`. |
| ZKTeco **M2-LR** | Tipe/kapabilitas harus **dikonfirmasi dari handshake**, jangan diasumsikan. |

Kedua tipe **mungkin memakai format template berbeda** (versi algoritma finger/face). Server tidak boleh mengirim template ke mesin yang tidak mendukungnya. Lihat Fase 0.

## 3. Stack (boleh diganti jika ada alasan kuat, tulis alasannya di `docs/decisions.md`)

- Python 3.12 + **FastAPI** (uvicorn), async
- **PostgreSQL 16** (wajib: butuh `ON CONFLICT`, `FOR UPDATE SKIP LOCKED`, transaksi)
- SQLAlchemy 2.x + Alembic (migrasi)
- Worker: proses terpisah (pakai tabel DB sebagai antrian, **tanpa Redis/Celery** di tahap awal agar state antrian ikut transaksi DB)
- Web admin: HTMX + Jinja2 sederhana, atau React kalau memang perlu. Prioritas rendah.
- Docker Compose: `app`, `worker`, `db`, `caddy/nginx` (reverse proxy)
- pytest + pytest-asyncio, ruff, mypy

Struktur repo:

```
adms/
  app/
    iclock/        # endpoint protokol mesin (cdata, getrequest, devicecmd, ...)
    parsers/       # parser ATTLOG, OPERLOG, USER, FP, BIODATA, dll
    services/      # attlog_ingest, enrollment, distribution, command_queue
    workers/       # command_dispatcher (timeout/retry), outbox_forwarder, offline_monitor
    api/           # REST untuk admin/web
    models/ db/
  tools/
    fake_device.py # simulator mesin (WAJIB, lihat Fase 1)
  tests/
  docs/
    protocol_notes.md   # temuan nyata dari mesin, diisi saat Fase 0
    decisions.md
  docker-compose.yml
  project.md
```

## 4. Aturan Kerja untuk Agent

1. **Jangan mengarang detail protokol.** Format baris ATTLOG/USER/FP/BIODATA bervariasi antar firmware. Yang tertulis di dokumen ini adalah **titik awal**; kebenaran final = hasil capture dari mesin asli (Fase 0). Catat temuan di `docs/protocol_notes.md`.
2. Kerjakan **per fase**. Setiap fase selesai ketika *acceptance criteria* terpenuhi dan test hijau. Commit per fase.
3. Jika ada ambiguitas yang mengubah desain data (bukan sekadar gaya), **berhenti dan tanya**, jangan menebak.
4. **Jangan pernah** mengirim command destruktif otomatis ke mesin (`CLEAR LOG`, `CLEAR DATA`, `DATA DELETE USERINFO` massal) kecuali lewat aksi eksplisit admin dengan konfirmasi.
5. Semua data mentah dari mesin disimpan dulu (append-only) **sebelum** diproses. Parser boleh gagal; data mentah tidak boleh hilang.
6. Jangan log isi template biometrik (`TMP=`) ke log aplikasi. Simpan di DB saja (terenkripsi, lihat bagian Keamanan).

---

## 5. Ringkasan Protokol iClock (titik awal, WAJIB diverifikasi)

Mesin yang bertindak sebagai client; server hanya menjawab. Endpoint umum:

| Endpoint | Fungsi |
|---|---|
| `GET /iclock/cdata?SN=<sn>&options=all&pushver=..&language=..` | Handshake/registrasi mesin. Server membalas blok konfigurasi (Stamp, Delay, ErrorDelay, TransFlag, Realtime, TimeZone, dll). |
| `POST /iclock/cdata?SN=<sn>&table=ATTLOG&Stamp=<n>` | Upload attendance log. Body: baris dipisah `\n`, kolom dipisah `\t`. **Balas `OK:<jumlah_baris_diproses>`** |
| `POST /iclock/cdata?SN=<sn>&table=OPERLOG&Stamp=<n>` | Upload operation log: user baru/ubah (`USER ...`), fingerprint (`FP ...`), face (`FACE ...`), `BIODATA ...`, `USERPIC`, `BIOPHOTO`, log operasi. |
| `POST /iclock/cdata?SN=<sn>&table=ATTPHOTO` | Foto saat absen (opsional, fase akhir). |
| `GET /iclock/getrequest?SN=<sn>&INFO=...` | Heartbeat + polling command. Balas `OK` bila kosong, atau `C:<cmd_id>:<COMMAND>`. |
| `POST /iclock/devicecmd?SN=<sn>` | Mesin melaporkan hasil command: `ID=<cmd_id>&Return=<kode>&CMD=<nama>`. Balas `OK`. |

Contoh baris ATTLOG (titik awal): `PIN \t YYYY-MM-DD HH:MM:SS \t Status \t Verify \t Workcode \t Reserved1 \t Reserved2`

Contoh command ke mesin (titik awal, tab = `\t`):

- `DATA UPDATE USERINFO PIN=1\tName=Budi\tPri=0\tPasswd=\tCard=\tGrp=1\tTZ=0000000100000000`
- `DATA UPDATE FINGERTMP PIN=1\tFID=0\tSize=..\tValid=1\tTMP=<base64>`
- `DATA UPDATE BIODATA Pin=1\tNo=0\tIndex=0\tValid=1\tDuress=0\tType=9\tMajorVer=..\tMinorVer=..\tFormat=..\tTmp=<base64>` (Type: 1 finger, 2 face NIR, 9 face visible light — verifikasi)
- `DATA QUERY USERINFO`, `DATA QUERY FINGERTMP`, `DATA QUERY BIODATA Type=9` (menarik data dari mesin)
- `DATA DELETE USERINFO PIN=1`
- `INFO`, `CHECK`, `REBOOT`, `SET OPTION key=value`

Handshake `options=all` juga melaporkan kapabilitas mesin (nama perangkat, firmware, dukungan finger/face/biodata/foto, versi algoritma). **Simpan semuanya ke `devices.capabilities` (JSONB)** dan gunakan untuk memutuskan template mana yang boleh dikirim ke mesin tersebut.

---

## 6. Model Data (minimum)

```
devices(
  id, serial_number UNIQUE, name, model, firmware, ip_last, tz_offset_minutes default 420,
  is_master bool, is_active bool, capabilities jsonb,
  last_seen_at, last_attlog_stamp, last_operlog_stamp, created_at
)

raw_requests(              -- append-only, audit & replay
  id bigserial, device_sn, method, path, query, body bytea/text,
  received_at, body_sha256, parse_status, parse_error
)

employees(
  id uuid, pin text UNIQUE,  -- PIN dipakai di mesin (angka, panjang dibatasi mesin)
  full_name, card_no, password_hash/enc, privilege, is_active, created_at, updated_at
)

biometric_templates(
  id uuid, employee_id, kind ('finger'|'face'), slot (FID/No/Index),
  format_version, template bytes (terenkripsi), template_sha256,
  source_device_sn, version int, created_at,
  UNIQUE(employee_id, kind, slot, template_sha256)
)

attlog(
  id uuid, device_sn, pin, punched_at timestamptz, punched_at_local timestamp,
  status, verify_type, workcode, raw_line, raw_request_id, received_at,
  UNIQUE(device_sn, pin, punched_at)           -- KUNCI ANTI-REDUNDANSI
)
attlog_conflicts(           -- kunci sama tapi field lain berbeda: simpan untuk review, jangan timpa
  id, attlog_id, raw_line, raw_request_id, received_at
)

device_commands(
  id bigserial (dipakai sebagai cmd_id ke mesin), device_sn,
  command_type, payload text, idempotency_key text,
  state ('pending'|'sent'|'acked'|'failed'|'dead'|'cancelled'),
  attempts int, max_attempts int, next_attempt_at, sent_at, acked_at, return_code, last_error,
  created_by, created_at,
  UNIQUE(device_sn, idempotency_key)           -- cegah command ganda
)

employee_device_sync(       -- status per karyawan per mesin
  employee_id, device_sn, user_synced_at, finger_version_synced, face_version_synced, state
  PRIMARY KEY(employee_id, device_sn)
)

outbox_events(              -- antrian ke sistem hilir (HR/payroll/webhook)
  id uuid, event_type, aggregate_id, payload jsonb, idempotency_key UNIQUE,
  state, attempts, next_attempt_at, delivered_at, last_error, created_at
)
```

---

## 7. DESAIN ANTI-REDUNDANSI ATTLOG (BAGIAN PALING KRITIS)

Prinsip: **protokol ADMS = at-least-once**. Mesin akan mengirim ulang jika tidak menerima `OK` yang valid, setelah mati listrik, atau saat Stamp di-reset. Maka server **harus idempotent**, bukan berharap mesin tidak mengirim dobel.

### 7.1 Aturan ingest

1. Terima POST → **simpan body mentah ke `raw_requests` dan commit** (transaksi pendek terpisah).
2. Parse seluruh baris. Satu **transaksi DB** untuk satu batch:
   `INSERT INTO attlog ... ON CONFLICT (device_sn, pin, punched_at) DO NOTHING`
   - Jika baris konflik dan field lain (status/verify/workcode) **berbeda** → catat ke `attlog_conflicts`, jangan timpa.
3. **Balas `OK:<n>` hanya SETELAH commit sukses.** `n` = jumlah baris yang dikonsumsi (baru + sudah ada), karena dari sisi mesin semuanya sudah aman di server.
4. Jika DB error / parse error fatal → balas **non-OK (HTTP 500)** supaya mesin mengirim ulang. **Jangan pernah** balas OK untuk data yang belum tersimpan.
5. Baris individual yang rusak: simpan di `raw_requests` + tandai `parse_status='partial'`, lanjutkan baris lain, jangan menggagalkan seluruh batch (tapi hitung dan log).
6. Insert event ke `outbox_events` **dalam transaksi yang sama** dengan insert attlog (transactional outbox), hanya untuk baris yang benar-benar baru (cek `RETURNING id`).

### 7.2 Skenario kegagalan yang harus teratasi

| Skenario | Perilaku yang benar |
|---|---|
| Listrik mesin mati setelah kirim batch, sebelum terima `OK` | Mesin kirim ulang batch yang sama → `DO NOTHING`, tanpa dobel. |
| Server commit sukses tapi response `OK` hilang (jaringan putus) | Sama seperti di atas. |
| Server crash di tengah batch | Transaksi rollback; mesin kirim ulang; tidak ada data setengah. |
| Jaringan putus berjam-jam, mesin menumpuk log | Setelah tersambung, batch besar masuk (bisa ratusan–ribuan baris). Harus diproses dalam chunk, tetap idempotent, dan response tidak timeout (batasi ukuran per transaksi, mis. 500 baris, tetap balas OK total setelah semua commit). |
| Stamp mesin direset / di-set ulang ke awal (re-upload seluruh log) | Seluruh riwayat masuk lagi → semuanya konflik → `DO NOTHING`. Tidak boleh ada dobel. |
| Mesin diganti / reset pabrik (SN sama, log lama hilang) | Aman. Tampilkan peringatan jika `Stamp` mundur drastis. |
| Mesin ganti SN (hardware replacement) | Admin memetakan SN lama→baru; data lama tetap utuh. |
| Dua mesin dipakai orang yang sama | **Bukan duplikat.** Kunci memuat `device_sn`, jangan dedup antar mesin. |
| User tap dua kali dalam beberapa detik | Tetap tersimpan semua di `attlog`. Dedup "debounce" hanya di **view/laporan** dengan jendela yang bisa dikonfigurasi (default 60 detik), jangan hapus data mentah. |
| Jam mesin salah / drift | Simpan `punched_at_local` apa adanya + `received_at`; hitung dan simpan skew; sediakan sinkronisasi waktu (lihat 9.4). Jangan "memperbaiki" waktu diam-diam. |
| Dua request paralel dengan batch yang sama (retry cepat) | Unique constraint + `ON CONFLICT` menjamin aman. Tidak butuh lock aplikasi. |

### 7.3 Zona waktu

Mesin mengirim waktu lokal tanpa zona. Simpan `punched_at_local` (naive) dan `punched_at` (timestamptz) dihitung dari `devices.tz_offset_minutes` (default WIB, +07:00). Kunci unik memakai `punched_at` hasil konversi yang deterministik. **Jika tz mesin diubah di kemudian hari, jangan hitung ulang kunci**; tambahkan field `tz_offset_at_receive`.

### 7.4 Antrian ke sistem hilir (outbox)

- Worker `outbox_forwarder`: `SELECT ... FOR UPDATE SKIP LOCKED` batch kecil, kirim, tandai `delivered`.
- **Idempotency key = `attlog.id`** (atau hash deterministik), dikirim di header supaya penerima bisa dedup juga.
- Retry dengan exponential backoff + jitter, `max_attempts`, lalu `dead` + alert. Event `dead` bisa di-replay manual.
- Urutan per karyawan/per mesin tidak wajib global, tetapi kirim berurutan `punched_at` dalam satu batch.
- Jika sistem hilir mati, antrian menumpuk di DB; ingest dari mesin **tidak boleh terganggu**.

---

## 8. Antrian Command ke Mesin

Mesin mengambil command lewat `getrequest`; server tidak bisa push.

1. Semua perubahan (user baru, template, hapus user) → baris di `device_commands` dengan `idempotency_key` deterministik (mis. `user:<pin>:v<version>`), `state='pending'`.
2. Saat `GET /iclock/getrequest`: ambil command `pending` yang `next_attempt_at <= now()` untuk SN itu dengan `FOR UPDATE SKIP LOCKED`, ubah ke `sent`, `attempts++`, balas `C:<id>:<payload>`. Kirim **satu command per poll** atau batch kecil (konfigurasikan; mulai dari 1 agar mudah di-debug).
3. Saat `POST /iclock/devicecmd`: cocokkan `ID`, simpan `Return` code; `Return=0` → `acked`. Selain 0 → `failed` + simpan kode, retry sesuai kebijakan.
4. **Timeout**: worker mengembalikan command yang `sent` tapi tidak ada ack setelah N menit (mis. 5) menjadi `pending` lagi (mesin mungkin mati sebelum mengeksekusi). Semua command harus **idempotent di sisi mesin** (`DATA UPDATE` = upsert, `DATA DELETE` aman diulang), jadi re-send aman.
5. Mesin offline: command tetap `pending`, tidak kadaluarsa kecuali `cancelled` oleh admin. Saat mesin online lagi, antrian dikuras berurutan.
6. Urutan wajib per karyawan: `USERINFO` dulu, baru template. Gunakan field `sequence`/dependensi, jangan kirim template sebelum user ada di mesin.
7. Jika versi terbaru sebuah karyawan sudah dibuat, command lama yang masih `pending` untuk kunci yang sama **di-supersede** (jangan kirim data usang).
8. Dead-letter: `max_attempts` terlewati → `dead`, tampil di dashboard, bisa di-retry manual.

---

## 9. Fitur Detail

### 9.1 Registrasi karyawan baru

- Web/API: buat karyawan (PIN, nama, kartu opsional, privilege, pilih mesin tujuan atau "semua").
- Validasi PIN: unik, hanya karakter/panjang yang didukung mesin (cek dari capability, batas umumnya numerik).
- Generate command `DATA UPDATE USERINFO` ke mesin tujuan (lewat antrian di bagian 8).
- Pendaftaran biometrik: dua jalur, keduanya harus didukung:
  1. **Enroll fisik di mesin master** → mesin mengirim USER/FP/BIODATA lewat OPERLOG → server simpan → distribusi (9.2).
  2. (Opsional, jika firmware mendukung) command enroll jarak jauh dari server.
- Karyawan yang dibuat langsung di mesin (bukan dari web) juga harus **tertangkap** lewat OPERLOG dan otomatis dibuat di `employees` (flag `needs_review`).

### 9.2 Distribusi finger & face dari master ke mesin lain

- Satu atau lebih mesin ditandai `is_master`. Perubahan template di master (OPERLOG `FP`/`FACE`/`BIODATA`) → simpan ke `biometric_templates` dengan `version` naik hanya jika `template_sha256` berubah.
- Fan-out: untuk tiap mesin target aktif **yang kompatibel** (cek `capabilities` & `format_version`), buat command `USERINFO` + template, `idempotency_key` mengandung versi.
- Jangan kirim balik ke mesin sumber (hindari loop). Jangan kirim template ke mesin yang tidak mendukung jenis/versi tersebut; tandai `employee_device_sync.state='incompatible'` dan tampilkan di UI.
- **Kompatibilitas SenseFace 4A vs M2-LR**: fase 0 harus menjawab (a) jenis biometrik apa yang didukung tiap mesin, (b) apakah format template finger/face sama. Jika berbeda, dokumentasikan dan **jangan** coba konversi sendiri; fallback = enroll ulang di mesin tersebut.
- Fitur tarik ulang (backfill): tombol admin "sinkronkan ulang semua dari master" memakai `DATA QUERY ...` untuk mengambil data, lalu fan-out. Harus aman dijalankan berulang (idempotent).
- Hapus karyawan: soft-delete di server, command `DATA DELETE USERINFO` ke semua mesin hanya setelah konfirmasi admin.

### 9.3 Monitoring

- `last_seen_at` diperbarui dari tiap `getrequest`/`cdata`.
- Status **offline** jika tidak terlihat > 3× `Delay` (konfigurasi, default 5 menit). Worker `offline_monitor` membuat alert (log + webhook).
- Dashboard minimal: daftar mesin, status, last seen, jumlah command pending/failed/dead, jumlah attlog hari ini, antrian outbox, selisih jam mesin.

### 9.4 Sinkronisasi waktu

Cek skew tiap handshake (`now() - device_time` jika tersedia dari header/INFO). Sediakan aksi admin untuk set waktu mesin. Jangan ubah jam otomatis tanpa konfigurasi eksplisit.

---

## 10. Keamanan & Privasi

- Data biometrik = **data pribadi spesifik** (UU PDP No. 27/2022 Indonesia). Template dienkripsi at-rest (AES-GCM via key dari env/secret manager, bukan di repo). Akses dibatasi, dan ada audit siapa melihat/mengekspor.
- Hanya terima SN yang **terdaftar** (whitelist) atau yang disetujui admin saat pertama muncul (`devices.is_active=false` sampai disetujui). SN tidak dikenal: simpan di `raw_requests` tetapi jangan proses.
- Banyak mesin hanya bisa HTTP polos. Taruh server di jaringan internal/VLAN atau di balik VPN; batasi IP sumber di reverse proxy. Jika firmware mendukung HTTPS, aktifkan dan catat versinya.
- Rate limit dan batas ukuran body per request (mis. 10 MB) di reverse proxy.
- Panel admin: autentikasi wajib, hash password argon2, session aman, CSRF.
- Backup database harian + uji restore. Retensi `raw_requests` dapat dikonfigurasi (jangan hapus attlog).

---

## 11. Rencana Fase

### Fase 0 — Discovery protokol (WAJIB pertama)
- Jalankan server minimal yang **hanya mencatat** semua request (method, path, query, header, body) ke `raw_requests` dan membalas `OK`/konfigurasi minimal.
- Arahkan 1 SenseFace 4A dan 1 M2-LR ke server (menu Comm → Cloud Server Setting: alamat server + port).
- Kumpulkan: isi handshake `options=all`, contoh ATTLOG, OPERLOG (user, fp, face/biodata), response `devicecmd`, kapabilitas tiap tipe.
- Tulis temuan di `docs/protocol_notes.md` (format baris nyata, nama field, versi, Type biometrik, ukuran template).
- **Acceptance**: dokumen perbandingan kapabilitas kedua mesin + sampel mentah tersimpan; keputusan kompatibilitas template ditulis.

### Fase 1 — Fondasi + ATTLOG anti-redundan
- Skema DB + migrasi, `raw_requests`, `devices`, handshake yang benar, ingest ATTLOG sesuai bagian 7.
- Bangun `tools/fake_device.py`: simulator yang dapat (a) mengirim batch ATTLOG, (b) mengulang batch yang sama, (c) memutus koneksi sebelum membaca response, (d) mengirim batch besar, (e) mereset Stamp.
- **Acceptance (otomatis di pytest)**:
  - Kirim batch yang sama 10× → jumlah baris `attlog` tetap sama.
  - Matikan koneksi sebelum `OK` lalu retry → tidak dobel, tidak hilang.
  - Simulasi crash DB di tengah batch → tidak ada data setengah; retry sukses.
  - Reset Stamp + kirim ulang seluruh riwayat → nol baris baru.
  - 5 request paralel dengan batch identik → tepat 1 set baris.
  - Baris rusak tidak menggagalkan baris lain; terlihat di `raw_requests.parse_status`.
  - Uji dengan mesin asli: cabut LAN 30 menit dan matikan listrik saat absen → setelah pulih, jumlah attlog di server = jumlah log di mesin (cocokkan manual), tanpa dobel.

### Fase 2 — Antrian command + registrasi karyawan
- `device_commands`, `getrequest`, `devicecmd`, timeout/retry/dead-letter, supersede.
- CRUD karyawan + push `USERINFO` ke mesin tujuan. Tangkap user yang dibuat langsung di mesin.
- **Acceptance**: karyawan baru dari web muncul di kedua mesin; mesin dimatikan saat command `sent` → setelah hidup, command diulang dan sukses tanpa dobel; command `pending` bertahan saat server restart.

### Fase 3 — Distribusi biometrik master → mesin lain
- Tangkap FP/FACE/BIODATA dari OPERLOG, simpan terenkripsi, versioning, fan-out sesuai kompatibilitas, tracking `employee_device_sync`.
- **Acceptance**: enroll wajah/jari di master → mesin target bisa verifikasi orang tersebut (uji fisik); tidak ada loop kirim balik; mesin tak kompatibel ditandai jelas; backfill ulang aman.

### Fase 4 — Outbox ke sistem hilir + monitoring
- `outbox_forwarder` (webhook/REST yang dapat dikonfigurasi), idempotency key, retry/backoff, dead-letter + replay.
- Offline monitor, dashboard status, metrik (Prometheus `/metrics` atau minimal endpoint health).
- **Acceptance**: sistem hilir dimatikan 1 jam → setelah hidup, semua event terkirim tepat sekali (cek idempotency di penerima tiruan); ingest dari mesin tidak terpengaruh selama itu.

### Fase 5 — Hardening
- Enkripsi template, whitelist SN, rate limit, backup/restore teruji, runbook operasional (`docs/runbook.md`: mesin offline, rekonsiliasi attlog, ganti mesin, reset Stamp), load test (mis. 50 mesin × 1000 log).

---

## 12. Definition of Done (keseluruhan)

- [ ] Tidak ada duplikasi attlog pada semua skenario di bagian 7.2 (dibuktikan test otomatis + uji fisik).
- [ ] Tidak ada kehilangan data: `OK` hanya dibalas setelah commit.
- [ ] Command ke mesin idempotent, bertahan restart server, dan pulih setelah mesin offline.
- [ ] Template biometrik terdistribusi hanya ke mesin kompatibel; tersimpan terenkripsi.
- [ ] Semua temuan protokol nyata terdokumentasi di `docs/protocol_notes.md`.
- [ ] `docker compose up` cukup untuk menjalankan seluruh sistem; ada `.env.example`.
- [ ] Runbook operasional tersedia.

## 13. Pertanyaan Terbuka (tanyakan ke pemilik proyek bila belum terjawab di Fase 0)

1. Berapa jumlah mesin total dan ada di berapa lokasi/subnet? Apakah ada mesin yang di jaringan terpisah (VPN/NAT)?
2. Mesin mana yang menjadi master? Apakah boleh lebih dari satu?
3. Sistem hilir apa yang menerima attlog (HR/payroll sendiri, API, database)? Format dan autentikasi?
4. Apakah foto saat absen (ATTPHOTO) perlu disimpan?
5. Apakah server di on-premise, VPS, atau cloud? Ada kebutuhan HTTPS/VPN?
6. Berapa lama retensi `raw_requests` dan attlog yang diinginkan?
