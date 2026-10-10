# Menjalankan di Server Ubuntu / Debian

Aplikasi ini tidak butuh internet, tidak butuh database server, dan cukup satu CPU kecil.
Yang dibutuhkan hanya **Python 3.11+** dan satu folder yang bisa ditulis.

---

## A. Cara cepat (disarankan) — satu perintah

Syarat versi Python: **3.9 ke atas** — Flask 3.1.3, Werkzeug 3.1.8, reportlab 5.0.1 dan
waitress 3.0.2 semuanya menuntut `>=3.9` (dibaca dari metadata PyPI resmi).
Ubuntu 22.04 (3.10) dan 24.04 (3.12) aman. **Ubuntu 20.04 bawaannya Python 3.8** sehingga
perlu Python 3.10: `add-apt-repository -y ppa:deadsnakes/ppa && apt-get update &&
apt-get install -y python3.10 python3.10-venv`, lalu jalankan pemasangan dengan
`PYTHON=python3.10 bash deploy/ubuntu-setup.sh`. `ubuntu-setup.sh` sudah menangani ini
otomatis bila ia menemukan Python 3.8.

```bash
# 1. unggah zip ke server, mis. ke folder sementara, lalu ekstrak
sudo apt-get update -qq && sudo apt-get install -y -qq unzip
sudo mkdir -p /opt/pbb && cd /opt/pbb
unzip -o /path/ke/pbb-petugas.zip          # menghasilkan /opt/pbb/pbb-petugas

# 2. instal jadi layanan (service) yang hidup terus & ikut nyala saat server reboot
cd /opt/pbb/pbb-petugas
sudo bash deploy/ubuntu-setup.sh
```

Skrip `deploy/ubuntu-setup.sh` mengulang dengan aman (boleh dijalankan 2x). Isinya:
memasang `python3-venv` bila belum ada → membuat user layanan `pbb` (tanpa shell login) →
menyalin kode ke `/opt/pbb-petugas` → membuat `.venv` + `pip install -r requirements.txt` →
memasang service `pbb-petugas.service` (systemd, auto-restart) → membuka folder `data`.

Cek hasil:

```bash
systemctl status pbb-petugas --no-pager
curl -I http://127.0.0.1:8088/login        # harus HTTP/1.1 200 OK
sudo journalctl -u pbb-petugas -n 40       # kalau ada masalah
```

Ganti port (mis. 80) atau nama layanan sebelum `ubuntu-setup.sh` dijalankan:

```bash
sudo PBB_PORT=80 bash deploy/ubuntu-setup.sh      # port 80 perlu root/cap_net_bind
```
Kalau sudah terpasang, cukup ubah `PBB_PORT` di `/etc/systemd/system/pbb-petugas.service`
lalu `sudo systemctl daemon-reload && sudo systemctl restart pbb-petugas`.

---

## B. Cara manual (tanpa systemd) — mis. di PC kantor / VPS tanpa akses root

```bash
cd pbb-petugas
bash start.sh --siap         # buat .venv + instal modul (sekali saja)
bash start.sh                # jalankan -> http://IP-server:8088
```

Selalu boleh pakai `bash start.sh` — tidak bergantung pada bit eksekusi. Kalau
`./start.sh` menolak dengan **Permission denied**, artinya hak jalannya hilang saat
pemindahan (zip dibuka di Windows/flashdisk/smb, atau folder mounted `noexec`):

```bash
chmod +x start.sh deploy/ubuntu-setup.sh   # sekali saja, setelah itu ./start.sh jalan
mount | grep " on $(df --output=target . | tail -1)"   # cek: ada "noexec" berarti folder itu
cd /opt/pbb-petugas && bash start.sh --siap && bash start.sh   # solusi: pakai folder yang boleh dieksekusi
```
Setelah `bash start.sh` jalan sekali, skrip ini mengembalikan bit eksekusinya sendiri,
jadi `./start.sh` berikutnya langsung jalan. Saat mengekstrak di Linux pakai
`unzip -o -X nama.zip` (pilihan `-X` menyimpan izin asli dari zip).

Berhenti: `Ctrl+C`. Berhenti: `Ctrl+C`. Bila `start.sh` sama sekali tidak mau dijalankan (folder `noexec`),
jalankan tiga perintah di bawah langsung — hasilnya sama persis:

```bash
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
./.venv/bin/python app.py --port 8088
```

Supaya tetap hidup setelah terminal ditutup, pakai salah satu:

```bash
nohup ./start.sh >> data/log.txt 2>&1 &          # sederhana
tmux new -s pbb -d './start.sh'                  # bisa dilog kembali: tmux a -t pbb
```

---

## C. Membuka akses ke 7 petugas

```bash
hostname -I                                        # catat IP server, mis. 192.168.1.50
sudo ufw allow from 192.168.1.0/24 to any port 8088 comment 'loket PBB internal'
# atau untuk semua jaringan (hindari bila server punya IP publik):
sudo ufw allow 8088/tcp
```

Petugas buka: `http://192.168.1.50:8088` (HP/Tablet juga bisa, selama satu jaringan).

**Server dengan IP publik:** jangan buka 8088 ke internet langsung. Taruh nginx di depan
dengan HTTPS + batasi IP, contoh ringkas:

```nginx
server {
  listen 443 ssl;
  server_name pbb.nganjukkab.go.id;
  ssl_certificate     /etc/letsencrypt/live/pbb.nganjukkab.go.id/fullchain.pem;
  ssl_certificate_key /etc/letsencrypt/live/pbb.nganjukkab.go.id/privkey.pem;
  client_max_body_size 64M;
  location / { proxy_pass http://127.0.0.1:8088;
               proxy_set_header Host $host;
               proxy_set_header X-Forwarded-Proto $scheme; }
}
```

---

## D. Langkah pertama setelah aplikasi hidup

1. Login `admin` / `admin123` → **ganti password** (ikon kanan atas → Ganti Password).
2. Menu **Pengaturan**: isi kop surat (nama desa/kelurahan, kecamatan, kabupaten, alamat),
   nama & NIP Lurah, nama petugas bendahara, nomor rekening. Nilai ini tercetak di kwitansi.
3. Menu **Petugas**: buat 7 akun petugas blok (satu user login per petugas) + tentukan
   penandatangan tiap blok.
4. Menu **Impor**: unggah `data ketetapan` Excel asli (kolom: NOMOR, NOP, TAHUN PAJAK,
   NAMA WAJIB PAJAK, ALAMAT OBJEK/WAJIB PAJAK, LUAS BUMI, LUAS BANGUNAN, PBB YG DIBAYAR,
   TANGGAL BAYAR, + kolom nama blok di ujung). Unduh contoh kolom lewat
   **Data → Unduh format impor** atau `contoh-file/ketetapan-pbb-2026.xlsx` di dalam zip.
   Impor ke-2 dan seterusnya bersifat **delta**: NOP lama yang tidak muncul ditawarkan
   dinonaktifkan, bukan dihapus, dan riwayat pembayaran tidak pernah hilang.
5. Menu **Realisasi / Sinkron**: unggah file Excel realisasi mingguan dari eSPPT, atau isi
   template cURL (lihat `doc/AUTOMASI-ESPPT.md`) lalu jalankan **Uji Koneksi** di
   Pengaturan sebelum mengaktifkan mode robot.

Data contoh (kalau hanya ingin mencoba-coba dulu):
```bash
/opt/pbb-petugas/.venv/bin/python app.py --seed-demo      # untuk cara manual: ./start.sh --seed
```

---

## E. Berkas, cadangan, dan pembaruan

| Hal | Lokasi (cara A) |
|---|---|
| Basis data (semua tabel) | `/opt/pbb-petugas/data/pbb.db` |
| Cadangan harian otomatis (60 terakhir) | `/opt/pbb-petugas/data/backup/` |
| Folder "tuslah" file Excel/CSV | `/opt/pbb-petugas/data/auto/` (diproses otomatis tiap 6 jam & saat file baru masuk) |
| Kwitansi PDF yang pernah dicetak | `/opt/pbb-petugas/data/cetak/` (boleh dibersihkan kapan saja) |
| Berkas log | output systemd: `journalctl -u pbb-petugas` |

Cadangan manual ke luar server (cron tiap malam, contoh):
```bash
echo '30 2 * * * root sqlite3 /opt/pbb-petugas/data/pbb.db ".backup /backup/pbb/pbb-$(date +\%F).db"' | sudo tee /etc/cron.d/pbb-backup
```
(`sudo apt-get install -y sqlite3`; kalau `sqlite3` tidak mau dipasang, cukup salin
`data/backup/` yang sudah dibuat aplikasi — keduanya aman karena memakai SQLite backup API.)

**Memperbarui aplikasi** (mis. ada perubahan dari saya): cukup ekstrak zip baru di folder
mana pun lalu jalankan skripnya dari sana — skrip menyalin ulang kode ke `/opt/pbb-petugas`
dan **tidak pernah** menyentuh `data/` maupun `.venv/` yang sudah ada.

```bash
cd /tmp && unzip -q /unduhan/pbb-petugas.zip -d pbb-baru
sudo bash /tmp/pbb-baru/deploy/ubuntu-setup.sh
sudo systemctl restart pbb-petugas && sudo journalctl -u pbb-petugas -n 15
```

Cara lama (rsync manual) masih boleh, asal jangan hapus `data/`:
```bash
sudo systemctl stop pbb-petugas
sudo rsync -a --exclude data --exclude .venv /opt/pbb-baru/pbb-petugas/ /opt/pbb-petugas/
sudo systemctl start pbb-petugas
```

---

## F. Kalau ada masalah

| Gejala | Penyebab umum & solusi |
|---|---|
| `ModuleNotFoundError: flask` | `.venv` belum dibuat di lokasi ini → `sudo bash deploy/ubuntu-setup.sh` atau `bash start.sh --siap` |
| `bash: ./start.sh: Permission denied` | bit eksekusi hilang saat pemindahan → `chmod +x start.sh` atau jalankan `bash start.sh`; bila folder mounted `noexec`, pindahkan ke `/opt` |
| `[!] Port 8088 sudah dipakai proses lain` | server ini sudah menjalankan layanan lain di port itu (mis. GenieACS/Docker) → `./start.sh --port 8089`, atau pakai 80/8080 lewat nginx |
| `cd: /pbb-petugas: No such file or directory` | folder aplikasi ada di dalam `/opt` (hasil `unzip` membuat subfolder) → `cd /opt/pbb-petugas`; kalau bingung, cari: `find / -name app.py -path "*pbb-petugas*" 2>/dev/null` |
| `Package requires a different Python: not '3.8'` / `ERROR: Could not find a version` | Flask 3.1.3 & reportlab 5.0.1 butuh Python >=3.9 (Ubuntu 20.04 bawaannya 3.8) → `PYTHON=python3.10 bash deploy/ubuntu-setup.sh` setelah memasang ppa:deadsnakes, atau pakai `Flask==2.3.3`/`reportlab==4.1.0` pada 3.8 (jalankan `./.venv/bin/pip install "Flask==2.3.3" "reportlab==4.1.0" "waitress==2.1.2"`) |
| `Address already in use` | port dipakai: `sudo ss -ltnp \| grep 8088` → hentikan proses lama atau pakai `PBB_PORT=8090` |
| Service aktif tapi `curl` 000 | aplikasi start di port lain → cek `Environment=PBB_PORT` di file service |
| Petugas tidak bisa akses | firewall/VPN: `sudo ufw status verbose`; pastikan satu jaringan WLAN/LAN |
| `data/` tidak bisa ditulis | `sudo chown -R pbb:pbb /opt/pbb-petugas/data && sudo chmod 750 /opt/pbb-petugas/data` |
| Kwitansi PDF gagal | font bawaan ReportLab hilang saat `.venv` rusak → `sudo /opt/pbb-petugas/.venv/bin/pip install -r requirements.txt` |
| Import Excel "NOP tidak dikenali" | sel NOP terpotong Excel (angka 0 di depan hilang) → format kolom jadi Text sebelum disimpan, atau biarkan: aplikasi melaporkan baris bermasalah supaya bisa diperbaiki manual lewat menu Data |
| Login hilang terus | jangan buka lewat `localhost` di komputer lain; semua petugas pakai alamat IP server yang sama (session cookie per browser, normal) |

Satu hal yang perlu diketahui: **jangan jalankan 2 instance pada satu folder `data/`**
(app.py manual + service systemd bersamaan). SQLite menahan tulis, tapi dua proses akan
saling merebut file sementara. Cukup satu layanan; menu `/monitor` menunjukkan statusnya.

---

## G. Uji mandiri setelah terpasang (opsional, dianjurkan sekali)

```bash
cd /opt/pbb-petugas
sudo -u pbb ./.venv/bin/pip install -q -r requirements-dev.txt   # pytest (pymupdf opsional)
sudo -u pbb ./.venv/bin/python -m pytest -q
```

Harapannya `24 passed`. Bila `pymupdf` gagal diunduh (server tanpa internet), hasilnya
`22 passed, 2 skipped` — kedua tes yang terlewat hanya memeriksa jumlah halaman PDF,
bukan logika aplikasi. Setelah selesai, folder uji boleh dibiarkan; tidak memengaruhi jalan.
