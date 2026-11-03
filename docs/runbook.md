# Runbook Operasional ADMS Server (Windows Server 2016)

Dokumen ini berisi prosedur standar operasional (SOP) dan penanganan insiden untuk ADMS Server ZKTeco.

---

## 1. Monitoring Harian & Status Layanan

### 1.1 Memeriksa Status Windows Services
Buka PowerShell sebagai Administrator:
```powershell
Get-Service ADMS-API, ADMS-Worker
```
Status normal adalah `Running`. Jika service berhenti, nyalakan kembali:
```powershell
Restart-Service ADMS-API
Restart-Service ADMS-Worker
```

### 1.2 Memeriksa File Log
File log terletak di folder `C:\ADMS\logs\`:
- `api_stdout.log` & `api_stderr.log`: Log akses HTTP dan parsing ZKTeco iClock.
- `worker_stdout.log` & `worker_stderr.log`: Log aktivitas pengiriman outbox, retry command, dan offline monitor.

---

## 2. Penanganan Insiden & Skenario Operasional

### 2.1 Mesin Absensi Offline / Putus Jaringan
**Gejala**: Mesin berstatus `Offline` di dashboard web (tidak terlihat > 90 detik).
**Penyebab**: Kabel LAN lepas, Wi-Fi terputus, IP server berubah, atau mesin mati listrik.
**Prosedur**:
1. Periksa konektivitas jaringan ke IP mesin: `ping <IP_MESIN>`.
2. Periksa apakah Windows Firewall memblokir port HTTP:
   `Get-NetFirewallRule -DisplayName "ADMS Server HTTP Port 80"`
3. Pada mesin fisik, cek menu **Comm. > Cloud Server Setting**:
   - Pastikan **Server IP** mengarah ke IP Windows Server yang benar.
   - Pastikan **Server Port** sesuai (default `80`).
4. **Setelah mesin kembali online**:
   - Mesin akan otomatis mengirimkan akumulasi log absensi yang tertampung di memori mesin.
   - Server memproses dalam chunk 500 baris secara transaksional dengan `ON CONFLICT DO NOTHING`.
   - **TIDAK AKAN ADA DATA GANDA**, meskipun mesin mengirimkan ulang log yang pernah dikirim sebelum mati.

---

### 2.2 Rekonsiliasi Jumlah ATTLOG (Pencocokan Data)
Jika ingin memastikan seluruh punch pada mesin fisik sudah tersimpan di database ADMS:
1. Catat jumlah Record pada mesin fisik: Menu **Sys Info > Data Capacity > Att. Records**.
2. Buka dashboard ADMS: `http://localhost/attlog?device_sn=<SERIAL_NUMBER>`.
3. Bandingkan jumlah total log untuk serial number tersebut:
   ```sql
   SELECT count(*) FROM attlog WHERE device_sn = 'SF4A23450001';
   ```
4. Jika jumlah di server lebih sedikit (misal mesin offline berhari-hari):
   - Kirim perintah `CHECK` lewat menu **Mesin Absensi > Check** pada dashboard, atau
   - Ubah Stamp di server menjadi 0 lalu kirim perintah CHECK agar mesin mengunggah riwayat lengkap. Mekanisme anti-redundan server menjamin tidak akan ada log duplikat.

---

### 2.3 Penggantian Mesin Rusak (Hardware Replacement)
Jika mesin fisik rusak dan diganti unit baru dengan Serial Number berbeda:
1. Hubungkan mesin baru ke jaringan dan atur Cloud Server Setting ke IP server ADMS.
2. Tunggu mesin melakukan handshake otomatis (akan muncul di dashboard ADMS).
3. Tandai mesin baru dengan model yang sesuai (SenseFace 4A atau M2-LR).
4. Jika mesin yang diganti adalah mesin MASTER:
   - Centang opsi `is_master` pada mesin baru lewat menu **Mesin Absensi**.
   - Klik tombol **Tarik dari Master** untuk menarik userinfo dan biometrik.
5. **Data absensi lama tetap tersimpan utuh** di tabel `attlog` dengan referensi serial number lama.
6. Untuk sinkronisasi seluruh karyawan yang terdaftar di server ke mesin baru:
   Buka menu **Karyawan**, klik simpan ulang atau gunakan endpoint API untuk push ke SN mesin baru.

---

### 2.4 Penanganan Stamp Reset
Jika firmware mesin di-upgrade atau mesin di-reset ke pengaturan pabrik sehingga Stamp counter kembali ke 0:
- Mesin akan mengirim ulang seluruh riwayat absensinya.
- Database ADMS secara otomatis mengeksekusi `ON CONFLICT (device_sn, pin, punched_at) DO NOTHING`.
- Data lama tidak akan terduplikasi.
- Server akan membalas `OK:<n>` sehingga mesin mengetahui datanya sudah aman dan menaikkan Stamp-nya kembali.

---

### 2.5 Pemulihan Antrian Command yang Gagal (Dead Letter Queue)
Jika command ke mesin melebihi batas percobaan (`attempts >= max_attempts`):
1. Status command akan menjadi `DEAD`.
2. Buka halaman web **Antrian Command** (`http://localhost/commands`).
3. Periksa kolom **Error Terakhir** untuk mengetahui penyebab kegagalan (misal: user PIN belum ada, format biometrik tidak didukung, atau memori mesin penuh).
4. Setelah penyebab diperbaiki, klik tombol **Retry** pada baris command tersebut. Status akan kembali ke `pending` dan otomatis dikirim saat mesin melakukan polling berikutnya.

---

## 3. Backup & Restore Database PostgreSQL

### 3.1 Backup Manual
Jalankan script PowerShell:
```powershell
.\scripts\backup_db.ps1
```
File backup `.sql` akan tersimpan di folder `C:\ADMS\backups\`.

### 3.2 Restore Database
Jika terjadi kerusakan sistem dan perlu me-restore data dari file backup:
```powershell
# Stop service ADMS terlebih dahulu
Stop-Service ADMS-API, ADMS-Worker

# Restore via psql
psql -h 127.0.0.1 -U postgres -d adms_db -f "C:\ADMS\backups\adms_db_YYYYMMDD_HHMMSS.sql"

# Nyalakan kembali service
Start-Service ADMS-API, ADMS-Worker
```
