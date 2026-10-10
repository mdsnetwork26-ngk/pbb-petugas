# Aplikasi Loket Pembayaran PBB-P2 — Panduan Pemakaian

Aplikasi untuk **penerima pembayaran PBB-P2 di kelurahan/desa** dengan 7 petugas blok.
Satu berkas database, jalan di komputer sendiri (bisa diakses 7 petugas lewat WiFi),
tanpa internet, tanpa langganan.

---

## 1. Masalah yang dipecahkan

| Sebelum | Sesudah |
|---|---|
| Ketetapan dipegang di Excel, pembayaran dicatat manual di kolom “TANGGAL BAYAR” | Ketetapan jadi basis data; tiap NOP punya status bayar sendiri yang tidak bisa tertimpa |
| Kwitansi ditulis tangan / buku kwitansi | Kwitansi A4 resmi (kop + nomor urut + terbilang + tempat tanda tangan) dicetak 1 klik, bisa unduh PDF untuk di-WA |
| Cek “sudah bayar atau belum” harus membuka eSPPT satu per satu | Satu kotak pencarian; data realisasi mingguan yang diunduh dipakai untuk menandai lunas otomatis |
| Laporan progres per petugas blok diketik ulang tiap minggu | Otomatis: SPPT lunas, uang masuk, mana yang bayar mandiri ke bank, mana yang uang petugasnya belum lunas |
| Pembaruan ketetapan 2–3 kali setahun berisiko menghapus catatan bayar | Impor ulang hanya mengubah nama/alamat/nominal/petugas; riwayat pembayaran tersimpan |

---

## 2. Menjalankan pertama kali (Windows)

1. Pasang Python 3.11+ dari https://www.python.org/downloads — **centang “Add python.exe to PATH”**.
2. Salin folder `pbb-petugas` ke komputer (mis. `D:\PBB`).
3. Klik dua kali **`Siap.bat`** — otomatis membuat `.venv` dan menginstal Flask, openpyxl, reportlab.
4. *(opsional, buat coba-coba)* klik **`Contoh Data.bat`** — mengisi 7 petugas blok, akun login,
   ±140 NOP, sebagian pembayaran, plus file Excel contoh di folder `contoh-file\`.
5. Klik **`Jalankan Aplikasi.bat`**. Jangan tutup jendela hitam itu (itu mesin aplikasinya).
6. Buka http://localhost:8088 → login `admin` / `admin123` → **ganti password** (tulis di kanan atas).

Untuk Linux/macOS: `./start.sh --siap` lalu `./start.sh` (atau `./start.sh --seed` untuk data contoh).

### Membuka untuk 7 petugas lewat WiFi
Petugas cukup membuka alamat IP komputer Anda di browser HP: `http://192.168.x.x:8088`
alamat IP tampil di jendela saat aplikasi dijalankan. Yang perlu dicek bila tidak bisa dibuka:
- PC dan HP satu jaringan WiFi yang sama;
- Windows Defender Firewall → izinkan jaringan **Private** untuk profil aplikasi, atau
  jalankan sekali di PowerShell (admin):
  `New-NetFirewallRule -DisplayName "Loket PBB" -Direction Inbound -LocalPort 8088 -Protocol TCP -Action Allow`
- Petugas dibuat punya akun sendiri (menu Petugas → centang “buatkan akun login”) supaya
  ia hanya melihat bloknya dan tidak salah menekan menu admin.
- Agar aplikasi tetap jalan saat layar terkunci: set PC jangan auto-sleep (Power & Battery → Sleep = Never).

Berhenti: tutup jendela. Nyalakan lagi: `Jalankan Aplikasi.bat` — data tetap utuh.

---

## 3. Alur kerja harian

### 3.1 Impor / perbarui ketetapan (2–3 kali setahun)
Menu **Impor Data → Data ketetapan** → pilih file Excel (bentuknya sama dengan tabel Anda:
`NOMOR · NOP · TAHUN PAJAK · NAMA WAJIB PAJAK · ALAMAT OBJEK · ALAMAT WP · LUAS BUMI ·
LUAS BANGUNAN · PBB YG DIBAYAR · NAMA PETUGAS BLOK`).

Yang terjadi:
- NOP baru dibuat; NOP lama diperbarui (nama, alamat, luas, nominal, petugas blok);
- **riwayat pembayaran & kwitansi tidak pernah dihapus** — bila nominal berubah, selisihnya
  terlihat di Laporan, tidak hilang;
- kolom **NAMA PETUGAS BLOK** yang isinya “1”, “Blok 1”, “BLOK-1”, atau nama orang, semuanya
  dikenali dan dicocokkan ke daftar petugas (menu Petugas). Blok baru dibuat otomatis;
- NOP yang tidak terbaca dilaporkan per baris (biasanya karena Excel mengubah NOP jadi angka dan
  0 di depan hilang → format kolomnya jadikan *Teks*).
- Centang “anggap file ini ketetapan penuh” bila daftar itu benar-benar lengkap untuk tahun ini:
  NOP yang tidak muncul di file ditandai nonaktif (mis. mutasi/hapus), tetap tidak dihapus.

Tombol **Unduh format .xlsx** menyediakan templat kosong + contoh isi.

### 3.2 Menerima pembayaran & mencetak kwitansi
Dari Beranda ada satu kotak besar: ketik NOP (atau nama WP) → **Cari & catat**.
- ketemu 1 NOP → langsung ke formulir bayar;
- ketemu beberapa → daftar pilihan;
- NOP sudah pernah dicatat → langsung dibuka kwitansi lamanya (tidak bisa dobel; bila perlu
  mengoreksi, batalkan dulu transaksi lama di menu Transaksi).

Isi tanggal terima uang, jumlah (boleh beda dari ketetapan bila ada setor sebagian),
metode (tunai petugas / bank / pos / transfer), lalu simpan. Berikutnya langsung halaman
**Cetak** (A4 1 lembar, kop + blok tanda tangan + kode verifikasi) dengan tombol *Cetak*
dan *Unduh PDF*.

Nomor kwitansi: `MM/TAHUN/NNN` (urut per bulan, tidak didaur ulang walau dibatalkan) — contoh `09/2026/012`.

### 3.3 Membuat billing di eSPPT
Menu **Antrean Billing** = daftar NOP yang uangnya sudah diterima tapi kodenya belum ada.
Tiga cara, silakan pakai yang paling nyaman:
1. **Tempel** — dari eSPPT/Excel, satu baris per NOP: `35.18.010001.0001-0 ; 2812026000123456`.
   Titik koma, tab, spasi, nomor urut di depan — semuanya dimaklumi. Kode = angka panjang terakhir.
2. **Tombol “buka eSPPT”** di tiap baris (halaman cek tagihan sudah membawa NOP).
3. **Otomatis penuh** (butuh penyesuaian, baca `doc/AUTOMASI-ESPPT.md`).

### 3.4 Verifikasi mingguan dari data realisasi
Unduh/ambil data realisasi dari eSPPT (menu “Realisasi Pajak Detail” /
`/realisasi_rekap_lurah`) → **Impor Data → Data realisasi** → Upload.

Yang terjadi otomatis:
- kwitansi yang NOP-nya muncul di realisasi → status jadi **LUNAS (terverifikasi)**;
- NOP lunas di eSPPT tapi **tidak** ada kwitansi petugas → dibuatkan transaksi “bayar mandiri”
  (bank/pos) supaya capaian blok tetap benar, tanpa kwitansi;
- nominal berbeda lebih dari Rp1.000 → dihitung “selisih” dan muncul di Laporan;
- NOP di realisasi yang tidak ada di ketetapan → dilaporkan, tidak diabaikan diam-diam.

Format file yang dibaca: kolom `NOP`, `TAHUN PAJAK`, `JUMLAH PEMBAYARAN` (atau `POKOK` + `DENDA`),
`TANGGAL PEMBAYARAN` — persis tabel eSPPT, maupun file hasil salin-tempel ke Excel (.xlsx / .csv).

### 3.5 Laporan progres per petugas blok
Menu **Laporan**: per blok ada SPPT target, lunas, dalam proses, belum bayar, % capaian (jumlah SPPT
dan nilai rupiah), setoran tunai petugas vs bayar mandiri, dan dua daftar yang biasanya bikin
selisih: “kwitansi keluar tapi belum lunas” serta “realisasi tanpa kwitansi petugas”.
Tombol **Ekspor Excel** dan **Cetak** tersedia. Menu **Setoran ke Kas** = uang yang masih ada di
tangan petugas dan harus segera disetor.

Papan **Progres** (`/monitor`) versi layar besar, menyegarkan sendiri tiap menit — cocok dibuka di
TV kantor atau dipakai petugas memantau peringkat blok.

---

## 4. Menu & hak akses

| Menu | Admin | Petugas |
|---|---|---|
| Beranda (cari NOP, catatan bayar, status sinkron) | ✔ | ✔ (bloknya) |
| Data Ketetapan | semua blok | hanya bloknya |
| Rekam pembayaran + kwitansi | semua blok (boleh memilih penyetor) | hanya bloknya |
| Transaksi, Cetak Ulang kwitansi | ✔ | ✔ (bloknya) |
| Antrean Billing + tempel kode | ✔ | lihat & tempel bloknya |
| Impor, Laporan, Realisasi, Petugas, Pengaturan | ✔ | — (403) |

Akun: `admin/admin123` (ganti!), petugas `petugas1…petugas7/pbb123` bila memakai data contoh.
Buat akun petugas dari menu **Petugas** → centang “buatkan sekalian akun login”. Password petugas
yang lupa bisa direset dari menu yang sama.

---

## 5. Data, cadangan, keamanan

- Semua data ada di **`data/pbb.db`** (SQLite satu berkas). Pindah komputer = salin folder ini.
- **Cadangan harian otomatis** ke `data/backup/pbb-YYYY-MM-DD.db` (disimpan maksimal 60 hari) +
  tombol “Buat cadangan sekarang” dan daftar unduhan cadangan di menu Pengaturan.
  Disarankan sekali-sekali menyalin folder `data\` ke flashdisk/hard disk luar.
- Berkas unduhan Anda tersimpan di `data/upload/tanggal/…` sehingga impor bisa ditelusuri ulang;
  riwayat impor (siapa, kapan, berapa baris, hasil) tampil di menu Impor Data.
- Aplikasi tidak mengirim apa pun ke internet kecuali Anda mengaktifkan otomatisasi eSPPT
  (lihat `doc/AUTOMASI-ESPPT.md`). Login memakai sesi + CSRF, password terenkripsi (hash).
  Karena dipakai lewat WiFi kantor, tetap ganti password admin dan jangan buka port ini ke internet.

## 6. Tanya-jawab singkat

**Q: Petugas salah catat, bagaimana memperbaikinya?** Menu Transaksi → “batal” pada baris itu
(menghapus = tidak, riwayat harus tersisa), lalu catat ulang. Admin juga bisa mengoreksi tanggal,
jumlah, metode, status, dan kode billing lewat “ubah”.

**Q: WP minta kwitansi susulan / hilang?** Menu *Cetak Ulang*, cari nomor kwitansi, cetak atau kirim PDF.

**Q: Ada NOP yang bayar langsung ke bank, petugas tidak tahu?** Tidak masalah: setelah realisasi
dimpor, ia tercatat sebagai “bayar mandiri” dan capaian blok tetap benar.

**Q: Nominal di ketetapan berubah di tengah tahun?** Impor ulang filenya. Kwitansi lama tetap menunjukkan
nominal yang benar-benar diterima; selisihnya tampil di Laporan untuk ditindaklanjuti.

**Q: Bisa dipakai beberapa petugas bersamaan?** Bisa — semuanya membuka alamat yang sama;
SQLite dipakai bersama. Nomor kwitansi tidak akan dobel (dicek saat menyimpan).

**Q: Printer tidak pas selembar?** Di dialog cetak pilih A4, skala *Default/100%*, matikan
“Headers and footers”. Atau pakai tombol **Unduh PDF** yang sudah dikunci satu halaman.

## 7. Batasan yang perlu diketahui sejak awal

- Pencetakan mengikuti printer dan browser; hasil paling seragam lewat **Unduh PDF**.
- Pengambilan data realisasi otomatis & pembuatan billing otomatis bergantung pada bentuk halaman
  eSPPT yang tidak ber-API — sudah disediakan jalur cURL/konfigurasi, tapi **belum diuji dengan akun
  asli**, jadi siapkan mode manual (tempel) sebagai cadangan. Detail & langkah uji:
  `doc/AUTOMASI-ESPPT.md`.
- Aplikasi ini alat catat-mencatat setoran, bukan pengganti pembukuan kas daerah; rekonsiliasi
  akhir tetap ke Bapenda/eSPPT.
