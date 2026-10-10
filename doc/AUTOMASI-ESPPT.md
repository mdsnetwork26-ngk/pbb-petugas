# Menautkan Aplikasi ke eSPPT Bapenda (esppt.nganjukkab.go.id)

Dokumen ini menjelaskan **apa yang sudah pasti**, **apa yang perlu dicocokkan di komputer Anda**,
dan **urutan uji yang aman** sebelum mengaktifkan pengambilan data / pembuatan billing otomatis.

## Kenapa tidak tinggal “sambungkan” saja?

Situs eSPPT-SIMPBB Bapenda Nganjuk:
- halaman depannya (`/`) membuka form “Cek Tagihan PBB-P2” dan tombol login ke `/login`;
- halaman login menyatakan hanya untuk Petugas PBB, Lurah, dan Kepala Desa (harus punya akun);
- tidak ada dokumentasi API/REST publik, dan data tabel (termasuk
  `/realisasi_rekap_lurah`) disajikan sebagai **tabel HTML hasil render server**, bukan endpoint JSON.

Karena itu aplikasi ini tidak memaksa satu cara, tapi menyediakan 3 lapis:

| Lapis | Cara | Risiko | Perlu diatur? |
|---|---|---|---|
| 0 | **Manual + tempel** (bawaan) | nol — aplikasi tidak menyentuh eSPPT | tidak |
| 1 | **Ambil data realisasi** otomatis | rendah (hanya membaca) | ya, 1 kolom |
| 2 | **Buat billing** otomatis (login robot) | sedang — menulis ke sistem kabupaten | ya, butuh “resep” |

Semua lapis dinonaktifkan sampai Anda mencentang **Aktifkan otomatisasi eSPPT** di
Pengaturan › Sinkronisasi eSPPT, dan tombol billing tetap dikunci oleh **mode simulasi (dry-run)**
sampai Anda mematikannya sendiri.

---

## Lapis 1 — mengambil data realisasi mingguan

### Cara A (paling kuat, 1 menit): tempel “Copy as cURL”
1. Buka eSPPT dan login, masuk ke halaman rekap/realisasi yang biasa Anda buka
   (mis. `Realisasi Pajak Detail`), atur filter Tahun/Periode seperti biasa.
2. Tekan **F12** → tab **Network** → klik tombol unduh/refresh di halaman itu sampai muncul
   permintaan baru (kalau tidak ada tombol unduh, cukup reload halaman; cari request bertipe `document`
   atau `xhr` yang membawa data tabel, biasanya nama file/URL mengandung `realisasi`, `export`, `data`, `laporan`).
3. Klik kanan baris request itu → **Copy** → **Copy as cURL (bash)**.
4. Tempel seluruhnya ke kolom **Template permintaan realisasi** di aplikasi → Simpan.
5. Cari angka tahun pada template (mis. `tahun=2026` atau `"tahun":"2026"`) dan ganti dengan
   `{tahun}`; bila ada tanggal, ganti dengan `{dari}` dan `{sampai}`.
6. Tekan **Uji koneksi → “koneksi + tarik realisasi”**. Yang diharapkan: baris
   `Ambil data realisasi — berhasil — terbaca N baris`.
7. Lalu jalankan **Antrean Billing → Sinkron sekali jalan** (centang “tarik & cocokkan realisasi”).

### Cara B — path + parameter
Kalau datanya bisa diambil dengan URL biasa (mis. `https://esppt.nganjukkab.go.id/realisasi_rekap_lurah`
dan filter berupa query string), cukup isi:
- **Path realisasi** = `/realisasi_rekap_lurah`
- **Parameter** = `{"tahun":"{tahun}","start":"0","length":"2000","draw":"1"}`
  (`draw`/`start`/`length` adalah parameter tabel DataTables — angka `length` besar berarti “tampilkan semua”;
  kalau `length` dibatasi server, sinkron akan mengambil halaman pertama saja. Solusi terbaik: pakai Cara A.)

Aplikasi memakai URL itu dengan **GET** lalu membaca tabel HTML atau JSON di dalamnya.
Format yang dikenali parser: baris yang punya **NOP 18 digit** + kolom **POKOK/DENDA/JUMLAH/TANGGAL
PEMBAYARAN** dengan header yang mirip-mirip (lihat `core/excel.py`, `_REALISASI_ALIASES`).
Jadi file unduhan apa pun selama ada NOP + tahun + jumlah + tanggal bisa dipakai — termasuk
cara manual (menu Impor Data).

### Lapis “regex” (hanya kalau perlu)
**Regex pemenggal data** dipakai bila responsnya bukan JSON bertabel maupun HTML bertabel,
misalnya teks mentah. Kosongkan saja bila Cara A/B sudah berhasil.

---

## Lapis 2 — membuat billing otomatis (login robot)

Yang dilakukan aplikasi saat antrean dijalankan: login → untuk tiap NOP mengirim “resep” billing →
mengambil kode billing dengan regex → menandai transaksi **terkirim** → semuanya dicatat di
**Log aktivitas eSPPT** (menu Pengaturan, bagian bawah).

### Yang perlu diisi
| Isian | Contoh | Catatan |
|---|---|---|
| User / Password eSPPT | akun petugas/lurah Anda | bisa juga pakai variabel lingkungan `PBB_ESPPT_USER`, `PBB_ESPPT_PASSWORD` (lebih aman, tidak tertulis di database) |
| Path login | `/login` | ubah bila halaman login memakai URL lain |
| Template request billing | hasil *Copy as cURL* dari tombol “buat billing” di eSPPT | **wajib** — tanpa ini aplikasi menolak |
| Regex pengambil kode billing | `"([0-9]{12,20})"` | sesuaikan dengan bentuk respons JSON/HTML eSPPT |
| Mode simulasi (dry-run) | biarkan **tercentang** dulu | selama tercentang, request billing **tidak pernah** dikirim |

### Langkah uji yang saya sarankan (±10 menit, 1 NOP)
1. Pengaturan › eSPPT: isi user/password, **centang “Aktifkan otomatisasi”**, biarkan dry-run.
   Simpan → **Uji koneksi: “koneksi + login”**. Target: `Login Petugas PBB — berhasil`.
   - Bila gagal karena nama field berbeda (aplikasi mencoba `username/password`, lalu `user/pass`,
     lalu `nis/password`), salin request login dari DevTools ke **Template request billing** tidak bisa
     dipakai untuk login; dalam hal ini pakai Cara A untuk realisasi dan biarkan billing manual —
     atau kirimkan saya teks “Copy as cURL” request login + request billing, nanti saya sesuaikan.
   - Bila gagal karena **captcha / OTP / sesi satu-perangkat**, otomasi penuh sebaiknya tidak dipakai;
     pakai Lapis 1 (data realisasi otomatis) + tempel kode billing manual. Itu sudah memangkas
     sebagian besar pekerjaan mingguan Anda.
2. Matikan dry-run. Buka eSPPT di tab lain untuk NOP pilihan Anda (NOP nominal kecil yang memang
   akan dibayar), lalu di aplikasi: **Antrean Billing** → centang 1 NOP → *Buat billing otomatis*.
3. Cek 3 hal: (a) muncul “Kode billing …” di daftar “Billing terakhir dibuat”; (b) kode itu benar-benar
   ada di eSPPT; (c) di Log aktivitas eSPPT statusnya 200/ok.
4. Kalau beres, jalankan untuk 5–10 NOP, baru seterusnya satu antrean penuh. Jeda antar NOP sudah
   tersedia (bawaan 900 ms) — naikkan bila server tampak keberatan.
5. Setelah WP membayar di bank/pos/loket, jalankan **Sinkron sekali jalan** untuk menandai lunas
   dari data realisasi.

### Yang tidak mungkin terjadi karena fitur ini
Tidak ada penghapusan/pengubahan data pembayaran di aplikasi Anda akibat gagal billing: kegagalan
hanya tercatat sebagai `gagal` di log, status transaksi tetap `menunggu_billing`. Tidak ada
biaya administrasi apa pun yang dihitung (sesuai kebijakan Anda). Dan aplikasi tidak akan mengirim
satu request pun selama **Aktifkan otomatisasi** belum dicentang.

---

## Lapis 3 — menjadwalkan (opsional)

Kalau PC kantor menyala rutin, jadwalkan sinkron tiap Minggu pagi:
1. Buat berkas `SinkronMingguan.bat` di folder aplikasi (lihat `doc/JALAN-OTOMATIS.md`),
2. Task Scheduler → *Create Basic Task* → Weekly → Sunday 07:00 → *Start a program* → berkas itu.

Bila otomasi belum bisa menlogin, sinkron terjadwal tetap berguna untuk **mencocokkan** file realisasi
yang Anda taruh di folder `data/auto/` setiap minggu (lihat dokumen tadi).
