# Menjalankan Sendiri / Mulai Otomatis / Penjadwalan

Semua perintah di bawah dijalankan dari folder aplikasi (`pbb-petugas`).

## 1. Jalan di latar belakang (tanpa jendela hitam)

Setelah `Siap.bat` dijalankan sekali:

```
start /min "" ".venv\Scripts\pythonw.exe" app.py
```
`pythonw` = Python tanpa konsol. Berhenti: Task Manager → cari *pythonw.exe* → End task.

Bila ingin hanya bisa dibuka dari komputer itu sendiri (petugas pakai cara lain), jalankan dengan
`--host 127.0.0.1`. Bila memakai port lain: `--port 8090` (atau set variabel `PBB_PORT`).

## 2. Mulai otomatis saat PC dinyalakan

1. Tekan `Win + R` → `shell:startup` → Enter.
2. Klik kanan → New → Shortcut, isi lokasi:
   `"D:\PBB\pbb-petugas\.venv\Scripts\pythonw.exe" app.py`
   (sesuaikan huruf drive & nama folder; “Start in” isi folder aplikasi).
3. Selesai — setiap PC kantor dinyalakan, aplikasi ikut jalan di `http://localhost:8088`.

Agar tetap bisa diakses saat layar terkunci, matikan sleep:
*Settings → System → Power → Screen and sleep → Sleep = Never* (AC/plugged in).

## 3. Penjadwalan sinkron data realisasi

### 3a. Folder jemput otomatis (tanpa perlu internet/akun eSPPT)
Setiap minggu, **salin** file unduhan realisasi dari eSPPT ke folder
```
pbb-petugas\data\auto\
```
Aplikasi memeriksa folder itu sendiri (maks. 15 menit sekali, proses tiap 6 jam), lalu memindahkan
file ke `data\auto\sudah-diproses\` dan menandai NOP-NOP yang lunas. Bisa dipaksa sekarang lewat
menu **Pengaturan → Folder jemput otomatis → “Proses sekarang”**.
Nama file mengandung `ketetapan`/`tagihan`/`sppt` → diperlakukan sebagai data ketetapan;
lainnya dianggap realisasi.

### 3b. Memicu lewat Task Scheduler (waktu pasti)
Buat berkas `SinkronMingguan.bat` di folder aplikasi:
```bat
@echo off
chcp 65001 >nul
cd /d "%~dp0"
".venv\Scripts\python" app.py --sync-once
```
Task Scheduler → *Create Basic Task* → Weekly → Minggu 07:00 → *Start a program* → berkas di atas.
`--sync-once` menjalankan login eSPPT + pembuatan billing antrean + penarikan realisasi sesuai
Pengaturan › eSPPT, lalu mencetak log ringkas ke konsol (biar Task Scheduler yang menyimpan
hasilnya bila ingin diarsip).

Kalau aplikasi sudah jalan, alternatifnya memanggil URL pemicu (tanpa login, pakai token yang
diisi di Pengaturan):
```
schtasks /create /tn "PBB-Sinkron" /sc weekly /d SUN /st 07:00 ^
  /tr "curl \"http://localhost:8088/data/auto/berkala?token=TOKEN\""
```

## 4. Pindah komputer / reinstal Windows

Yang perlu dibawa **hanya folder aplikasinya** — termasuk `data\pbb.db` (semua catatan)
dan `data\backup\` (cadangan harian). Lalu:
```
Siap.bat        (instal ulang modul Flask/openpyxl/reportlab)
```
...dan jalankan seperti biasa. `.venv` tidak perlu (dan memang tidak bisa) dipindah.

## 5. Pulih dari masalah

| Gejala | Yang dilakukan |
|---|---|
| Salah input besar / mau mulai dari nol | menu Pengaturan → “Buang data pembayaran contoh” (ketetapan tetap aman), atau pulihkan cadangan |
| File `data\pbb.db` korup (mis. listrik padam saat menulis) | tutup aplikasi, hapus `data\pbb.db-wal` & `pbb.db-shm` bila ada, lalu ganti `pbb.db` dengan berkas terakhir dari `data\backup\pbb-YYYY-MM-DD.db` |
| Lupa password admin | hentikan aplikasi, jalankan `".venv\Scripts\python" tools\reset_admin.py` — password admin jadi `admin123` lagi |
| Aplikasi tidak bisa dibuka dari HP | cek WiFi satu jaringan, cek firewall (lihat `doc/PANDUAN.md` bagian WiFi) |
| Port 8088 sudah dipakai aplikasi lain | `".venv\Scripts\python" app.py --port 8090` |
