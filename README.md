# pbb-petugas — Aplikasi Loket Pembayaran PBB-P2

Aplikasi penerimaan pembayaran PBB-P2 untuk petugas pemungut 7 blok (kelurahan/desa),
jalan di satu komputer (localhost) dan diakses petugas lain lewat WiFi/LAN.
Butuh Python 3.9+ (Ubuntu 22.04/24.04 aman; Ubuntu 20.04 lihat `doc/DEPLOY-UBUNTU.md`).

```
Siap.bat               sekali saja: instal Python modul (Flask, openpyxl, reportlab, waitress)
Contoh Data.bat        opsional: isi data contoh + membuat berkas Excel contoh
Jalankan Aplikasi.bat  menjalankan aplikasi -> http://localhost:8088
```

Jalan di **server Ubuntu/Debian** (diinginkan bila 7 petugas akses lewat jaringan kantor):

```bash
sudo bash deploy/ubuntu-setup.sh      # sekali: salin ke /opt/pbb-petugas, .venv, service systemd, auto-restart
# atau tanpa root:
bash start.sh --siap && bash start.sh # pakai "bash ..." bila ./start.sh menolak ("Permission denied")
```
Detail, firewall, nginx/HTTPS, cadangan & pemulihan: `doc/DEPLOY-UBUNTU.md`.

Fitur tambahan (pembenahan 2026-10):
- **Halaman petugas** — hanya NOP bloknya sendiri, **belum bayar didahulukan** (urutan bawaan),
  kartu besar **"Masih harus ditagih"** + rincian denda berjalan; sortir bisa diubah (NOP, nama,
  tagihan terbesar/terkecil, denda terbesar, terbaru). *Bug lama: filter "Belum bayar" selalu
  kosong — sudah diperbaiki.*
- **Pindah blok wajib pajak** — petugas mengajukan (menu Pindah Blok / tombol ⇄ di baris NOP),
  admin menyetujui/menolak; NOP beserta riwayatnya pindah ke petugas blok baru begitu disetujui.
- **Laporan per blok** — kini memuat kolom **Kekurangan (Rp)** per petugas (ikut di ekspor Excel).
- **Denda otomatis setelah jatuh tempo** — setelah bulan jatuh tempo terlampaui (bawaan: Agustus),
  kolom **Denda** muncul di Data Ketetapan dan ikut ditagihkan (kwitansi, laporan, target blok).
  Rumus: pokok x persen x bulan terlampaui — bawaan 2%/bulan, maks 24 bulan; diatur di
  **Pengaturan > Denda & jatuh tempo**. Tagihan yang sudah tercatat bayar denda-nya dibekukan.

Dokumen:
- `doc/PANDUAN.md` — pemakaian sehari-hari (impor ketetapan, kwitansi, realisasi, laporan)
- `doc/AUTOMASI-ESPPT.md` — menautkan ke esppt.nganjukkab.go.id (realisasi & billing), langkah uji
- `doc/JALAN-OTOMATIS.md` — start otomatis, Task Scheduler, folder `data/auto`, pulih dari masalah
- `doc/UJI.md` — daftar pengujian otomatis (pytest) dan apa yang dijaminnya
- `doc/DEPLOY-UBUNTU.md` — pasang di server Ubuntu: systemd, port, firewall, cadangan, Update

Struktur:
```
app.py                routes Flask (beranda, data, bayar, kwitansi, billing, laporan, pengaturan)
core/db.py            skema SQLite + pengaturan + backup harian (data/pbb.db)
core/excel.py         pembaca toleran Excel/CSV (deteksi baris header, padanan nama kolom)
core/format.py        Rp, tanggal, terbilang, normalisasi NOP
core/pdf.py           kwitansi A4 (kop + tanda tangan/cap) dengan ReportLab
core/services.py      impor/rekonsiliasi, antrean billing, laporan per blok, sinkron latar belakang
core/esppt.py         adapter eSPPT (login, tarik realisasi, buat billing lewat template cURL)
core/auth.py          login, peran admin/petugas, CSRF
tools/seed_demo.py    data contoh + pembuat berkas Excel contoh
tools/reset_admin.py  reset password admin
tests/test_app.py     24 pengujian (pytest)
data/                 basis data, cadangan, berkas unduhan (di luar git)
pratinjau/            gambar tampilan halaman (dibuat tools/pratinjau.py)
```

Perintah lain: `python app.py --sync-once` (sinkron eSPPT sekali jalan dari terminal),
`python app.py --port 8090`, `python app.py --debug`.
