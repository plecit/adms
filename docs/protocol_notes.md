# Catatan Protokol iClock / ZKTeco Push Protocol

Dokumen ini mendokumentasikan spesifikasi protokol iClock ZKTeco yang digunakan oleh mesin absensi seperti **SenseFace 4A** (Visible Light Face) dan **M2-LR** (Multi-biometrik / RFID).

---

## 1. Alur Siklus Hidup Komunikasi (Lifecycle)

```
[Mesin ZKTeco]                                     [ADMS Server]
      |                                                  |
      | 1. Handshake (options=all)                       |
      |------------------------------------------------->|
      |    GET /iclock/cdata?SN=...&options=all          |
      |                                                  |
      |    Response: Blok konfigurasi key=value          |
      |<-------------------------------------------------|
      |                                                  |
      | 2. Heartbeat & Command Polling (setiap ~30s)     |
      |------------------------------------------------->|
      |    GET /iclock/getrequest?SN=...                 |
      |                                                  |
      |    Response: "OK" atau "C:<cmd_id>:<COMMAND>"    |
      |<-------------------------------------------------|
      |                                                  |
      | 3. Eksekusi Command & Pelaporan Status           |
      |------------------------------------------------->|
      |    POST /iclock/devicecmd?SN=...                 |
      |    Body: ID=<cmd_id>&Return=0&CMD=...            |
      |                                                  |
      |    Response: "OK"                                |
      |<-------------------------------------------------|
      |                                                  |
      | 4. Upload Attendance Log (ATTLOG)                |
      |------------------------------------------------->|
      |    POST /iclock/cdata?SN=...&table=ATTLOG        |
      |    Body: PIN \t Time \t Status \t Verify ...     |
      |                                                  |
      |    Response: "OK:<n>" (hanya setelah commit DB!) |
      |<-------------------------------------------------|
      |                                                  |
      | 5. Upload Operation Log (OPERLOG: User/Biometrik)|
      |------------------------------------------------->|
      |    POST /iclock/cdata?SN=...&table=OPERLOG       |
      |    Body: USER ... atau FP ... atau BIODATA ...   |
      |                                                  |
      |    Response: "OK:<n>"                            |
      |<-------------------------------------------------|
```

---

## 2. Handshake (`GET /iclock/cdata?SN=<sn>&options=all`)

### Parameter Query yang Dikirim Mesin:
- `SN`: Serial number mesin (contoh: `SF4A23450001`)
- `options=all`: Permintaan opsi konfigurasi server
- `pushver`: Versi push protocol (contoh: `2.4.1` atau `3.1.1`)
- `language`: Kode bahasa (contoh: `83` untuk Bahasa Indonesia, `69` untuk English)
- `pushprotver`: Versi spesifik fitur push protocol

### Respon Wajib Server:
Format respon adalah baris teks `Key=Value\n`:
```text
GET OPTION FROM: <SN>
Stamp=0
OpStamp=0
PhotoStamp=0
ErrorDelay=30
Delay=30
TransTimes=00:00;14:05
TransInterval=1
TransFlag=TransData AttLog\tOpLog\tAttPhoto\tEnrollUser\tChgUser\tEnrollFP\tChgFP\tFace
Realtime=1
Encrypt=0
ServerVersion=3.1.1
TimeZone=420
```

### Opsi Kritis:
- `Stamp`: Timestamp/counter ATTLOG terakhir yang diketahui server. Jika bernilai 0, mesin mengirim dari awal atau dari stamp terakhirnya.
- `OpStamp`: Counter OPERLOG terakhir.
- `Delay`: Interval polling heartbeat (dalam detik) ketika idle.
- `ErrorDelay`: Interval retry jika terjadi error koneksi.
- `TransFlag`: Fitur data apa saja yang diizinkan untuk di-push oleh mesin.
- `Realtime=1`: Menginstruksikan mesin mengirim punch log seketika saat karyawan tap (bukan hanya batching berkala).
- `TimeZone`: Offset zona waktu dalam menit (contoh: `420` = UTC+7 / WIB).

---

## 3. Format Data Masuk (Upload)

### 3.1 Attendance Log (`table=ATTLOG`)
Request: `POST /iclock/cdata?SN=<sn>&table=ATTLOG&Stamp=<stamp>`
Header: `Content-Type: text/plain`

Body baris dipisah oleh `\n`, kolom dipisah oleh `\t` (TAB):
```text
PIN	Time	Status	Verify	Workcode	Reserved1	Reserved2
101	2026-11-03 08:00:15	0	1	0	0	0
102	2026-11-03 08:02:40	0	15	0	0	0
```

Arti Field:
- `PIN`: ID karyawan pada mesin (string numerik, 1-9 digit tergantung kapasitas mesin).
- `Time`: Waktu lokal mesin `YYYY-MM-DD HH:MM:SS`.
- `Status`: Jenis punch:
  - `0`: Check-In (Masuk)
  - `1`: Check-Out (Kelang)
  - `2`: Break-Out (Mulai Istirahat)
  - `3`: Break-In (Selesai Istirahat)
  - `4`: Overtime-In (Masuk Lembur)
  - `5`: Overtime-Out (Keluar Lembur)
- `Verify`: Metode verifikasi:
  - `1`: Fingerprint
  - `2`: PIN / Password
  - `3`: Card (RFID)
  - `15`: Face (Visible Light / NIR)
- `Workcode`: Kode kerja / shift (default: `0`).

Respon Server:
- `OK:<jumlah_baris_berhasil_disimpan>` (Contoh: `OK:2`)
- **PENTING**: Jika terjadi error DB atau commit gagal, server wajib membalas HTTP 500 (BUKAN `OK:0`), agar mesin ZKTeco mengirim ulang.

---

### 3.2 Operation Log (`table=OPERLOG`)
Request: `POST /iclock/cdata?SN=<sn>&table=OPERLOG&Stamp=<stamp>`

Berisi log pendaftaran atau perubahan user di mesin fisik:

#### a. Data User Baru/Update:
```text
USER PIN=101	Name=Budi Santoso	Pri=0	Passwd=	Card=12345678	Grp=1	TZ=0000000100000000
```
- `PIN`: User PIN
- `Name`: Nama lengkap
- `Pri`: Hak akses (0: Normal User, 14: Super Admin)
- `Card`: Nomor kartu RFID

#### b. Template Sidik Jari (Legacy):
```text
FP PIN=101	FID=0	Size=1560	Valid=1	TMP=eJz...base64...
```

#### c. Template Biometrik Universal (`BIODATA` - Digunakan SenseFace 4A):
```text
BIODATA Pin=101	No=0	Index=0	Valid=1	Duress=0	Type=9	MajorVer=12	MinorVer=0	Format=0	Tmp=base64string==
```
- `Type`:
  - `1`: Fingerprint (ZKFP)
  - `2`: Face (NIR - Near Infrared lama)
  - `9`: Face Visible Light (SenseFace 4A / Horus / SpeedFace)
  - `8`: Palm (Telapak tangan)
- `MajorVer` & `MinorVer`: Versi algoritma biometrik (misal `12.0`). **Hanya mesin dengan versi kompatibel yang boleh menerima template ini.**

Respon Server: `OK:<jumlah_baris>`

---

## 4. Format Perintah ke Mesin (Device Commands)

Server mengirimkan command saat mesin melakukan polling `GET /iclock/getrequest?SN=<sn>`.

### 4.1 Respon Polling:
- Jika tidak ada command: `OK`
- Jika ada command: `C:<cmd_id>:<COMMAND>`
  (Contoh: `C:452:DATA UPDATE USERINFO PIN=101\tName=Budi\tPri=0\tCard=12345678`)

### 4.2 Daftar Command Penting:
1. **Update / Tambah User:**
   ```text
   DATA UPDATE USERINFO PIN=101	Name=Budi Santoso	Pri=0	Passwd=	Card=12345678	Grp=1	TZ=0000000100000000
   ```
2. **Kirim Template Sidik Jari:**
   ```text
   DATA UPDATE FINGERTMP PIN=101	FID=0	Size=1560	Valid=1	TMP=<base64>
   ```
3. **Kirim Template Face Visible Light (SenseFace 4A):**
   ```text
   DATA UPDATE BIODATA Pin=101	No=0	Index=0	Valid=1	Duress=0	Type=9	MajorVer=12	MinorVer=0	Format=0	Tmp=<base64>
   ```
4. **Hapus User:**
   ```text
   DATA DELETE USERINFO PIN=101
   ```
5. **Kueri Data dari Mesin Master (Backfill):**
   ```text
   DATA QUERY USERINFO
   DATA QUERY BIODATA Type=9
   DATA QUERY FINGERTMP
   ```
6. **Perintah Kontrol Mesin:**
   - `CHECK`: Mesin segera melakukan sinkronisasi data/waktu.
   - `INFO`: Mesin mengirimkan info perangkat lengkap.
   - `REBOOT`: Restart mesin.
   - `SET OPTION Delay=30`: Mengubah konfigurasi internal mesin.

---

## 5. Pelaporan Hasil Command (`POST /iclock/devicecmd`)

Setelah mesin mengeksekusi command, mesin mengirimkan status via POST:
```text
ID=452&Return=0&CMD=DATA UPDATE USERINFO
```
- `ID`: ID command (`cmd_id`)
- `Return`:
  - `0`: Sukses
  - `<0` atau `>0`: Gagal (misal: `-1` parameter error, `1` memori penuh, dll)
- Server membalas: `OK`

---

## 6. Komparasi Karakteristik Mesin

| Fitur | ZKTeco SenseFace 4A | ZKTeco M2-LR |
|---|---|---|
| **Kategori Utama** | Visible Light Facial Recognition Terminal | Multi-Biometric / RFID Attendance Terminal |
| **Biometrik Primer** | Face (Visible Light 0.3m - 2m) | Fingerprint (ZKFinger) + Kartu RFID |
| **Protokol Face** | `BIODATA Type=9` | Tidak ada / NIR legacy (`FACE` atau `Type=2`) |
| **Versi Push** | iClock 2.4.x / 3.x Standar ADMS | iClock 2.x Standar ADMS |
| **Toleransi Jaringan** | Reconnect otomatis, buffer offline hingga ribuan log | Buffer offline hingga ribuan log |
| **Aturan Distribusi** | Template Face dikirim hanya ke SenseFace 4A atau terminal Type 9 yang setara | Template FP dikirim hanya ke terminal yang mendukung Fingerprint |
