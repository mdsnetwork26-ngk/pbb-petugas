# Berkas Uji (pytest) — apa yang dijamin aplikasi

## Menjalankan

```
.venv\Scripts\python -m pip install pytest pymupdf          # sekali saja
.venv\Scripts\python -m pytest -q
```
Perlu `pymupdf` hanya untuk memeriksa hasil cetak PDF (jumlah halaman & isinya). Hasil akhir saat
dokumen ini ditulis: **23 pengujian lulus** (`23 passed`), tiap pengujian memakai database sementara
tersendiri sehingga tidak menyentuh `data/pbb.db`.

## Yang diuji

| Berkas uji | Menjamin |
|---|---|
| `test_format_dasar` | pemisah ribuan Rupiah, tanggal Indonesia, terbilang (mis. “dua ratus delapan puluh lima ribu rupiah”) |
| `test_norm_nop` | NOP 17 digit bentuk kelurahan (`35.18.010001.0001-0`) dan 18 digit bentuk panjang dibaca sama, NOP rusak → ditolak (bukan dianggap NOP lain) |
| `test_esppt_parse_curl_dan_placeholder` | tempelan “Copy as cURL” dari DevTools benar-benar diurai (URL, method, header, body) & `{nop}`/`{tahun}` terisi |
| `test_esppt_parse_realisasi_html_dan_json` | tabel HTML `/realisasi_rekap_lurah` (pokok/denda/jumlah/tanggal) dan respons JSON sama-sama terbaca |
| `test_template_curl_dipakai_untuk_realisasi_dan_billing` | template realisasi & billing dari cURL dipakai; kode billing diambil dengan regex |
| `test_esppt_dry_run_dan_setting_mati` | **pengaman penting**: selama otomatisasi mati atau dry-run, jumlah request keluar = 0 |
| `test_impor_ketetapan_toleran_variasi` | impor file berheader-judul 2 baris; impor ulang tidak merusak data, hanya mengubah nominal (1 baris berubah, 1 baris “sama”) |
| `test_impor_baris_nop_rusak_dilaporkan` | baris NOP tidak valid tidak hilang diam-diam, tapi dilaporkan + diberi saran perbaikan |
| `test_petugas_blok_dipetakan_dan_dibuat_otomatis` | “1”, “Blok 1”, “BLOK-1”, dan nama petugas → orang yang sama; blok baru dibuat otomatis |
| `test_folder_auto_menelan_file` | file di `data/auto/` terimpor otomatis, dipindah agar tidak terhitung dua kali, file rusak ditandai GAGAL |
| `test_alur_lengkap_dari_impor_sampai_lunas` | alur utuh: impor ketetapan → rekam bayar → kwitansi bernomor urut → PDF 1 halaman → antrean billing → tempel kode → impor realisasi → LUNAS → laporan capaian blok |
| `test_realisasi_menandai_bayar_mandiri` | WP yang bayar sendiri ke bank tercatat sebagai “bayar mandiri”, capaian blok tetap benar, tanpa kwitansi petugas |
| `test_selisih_kwitansi_dan_setoran_tertahan` | kwitansi keluar tapi belum lunas muncul di daftar “perlu dicek” & halaman setoran |
| `test_batal_kwitansi_nomor_tidak_daur_ulang` | pembatalan menandai `void` (riwayat tersisa), dan nomor kwitansi berikutnya tidak dipakai ulang |
| `test_kwitansi_pdf_semua_metode` | semua jalur bayar (tunai/bank/pos/transfer) bisa dicetak, semuanya pas 1 halaman |
| `test_semua_halaman_admin_terbuka` | 20+ halaman tidak error (termasuk unduhan xlsx/CSV laporan) |
| `test_jalan_pintas_cari_nop` | satu kotak pencarian: 1 NOP → formulir bayar, banyak → daftar, tidak ada → peringatan, sudah bayar → langsung kwitansi |
| `test_cek_kwitansi_publik` | warga bisa memverifikasi nomor kwitansi tanpa login; nomor tak dikenal → 404 |
| `test_petugas_hanya_blok_sendiri` | akun petugas: menu admin menolak (403), hanya NOP bloknya yang tampil |
| `test_csv_dan_tolak_password_salah` | ekspor CSV; password salah ditolak |
| `test_token_csrf_diperlukan` | POST tanpa token CSRF ditolak (400) — pelindung dari formulasi palsu di jaringan |
| `test_pengaturan_esppt_tersimpan_dan_dipakai` | pengaturan eSPPT tersimpan, form menampilkan nilai benar, “uji koneksi” menampilkan hasil (gagal maupun sukses) dengan rapi |

## Cara uji yang tidak bisa digantikan pytest

Dua hal hanya bisa dites di komputer Anda dengan data/akun nyata:
1. **Cetak ke printer** (kertas A4, posisi kop & tanda tangan, satu lembar). Buka Kwitansi → Cetak;
   bila mau lebih pasti, pakai tombol **Unduh PDF**.
2. **Login & billing ke eSPPT** — lihat urutan uji di `doc/AUTOMASI-ESPPT.md` (mulai dry-run, 1 NOP
   nominal kecil, cek log). Sebelum itu dipastikan aman: tanpa centang “Aktifkan otomatisasi” dan
   dengan dry-run, aplikasi tidak mengirim apa pun ke internet (dijamin `test_esppt_dry_run_dan_setting_mati`).
