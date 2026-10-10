"""Peraga (data contoh) + pembuat file Excel contoh sesuai format asli.

Jalankan:  python app.py --seed-demo
Data yang dibuat: 7 blok petugas + akun login + ±140 NOP ketetapan + sebagian pembayaran/realisasi,
sehingga aplikasi bisa dicoba (cetak kwitansi, laporan, rekonsiliasi) tanpa file asli.
"""
from __future__ import annotations

import datetime as dt
import random
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent

DESA = "Kelurahan Ketaman Ganggo Hilir"
KEC = "Kecamatan Nganjuk"
KAB = "Kabupaten Nganjuk"

BLOK = [
    ("Blok 1", "Sutrisno", "Jl. Merdeka, Jl. Diponegoro"),
    ("Blok 2", "Sri Wahyuni", "Jl. Kenanga, Jl. Melati"),
    ("Blok 3", "Bambang Haryanto", "Jl. Garuda, Jl. Elang"),
    ("Blok 4", "Endang Lestari", "Jl. Anggrek, Jl. Dahlia"),
    ("Blok 5", "Agus Salim", "Jl. Mawar, Jl. Flamboyan"),
    ("Blok 6", "Rina Kartika", "Jl. Cempaka, Jl. Bougenville"),
    ("Blok 7", "Dwi Purnomo", "Jl. Kenari, Jl. Nusa Indah"),
]

DEPAN = ["Slamet", "Siti", "Budi", "Endang", "Joko", "Sri", "Agus", "Dwi", "Rina", "Suparman",
         "Ngatemi", "Marsono", "Ponirah", "Sugeng", "Wahyu", "Kasmiran", "Tuminah", "Hariyadi"]
BELAKANG = ["Wibowo", "Hartono", "Utami", "Prasetyo", "Rahayu", "Santoso", "Kusuma", "Hidayat",
            "Susilo", "Mulyani", "Setiawan", "Lestari", "Handoko", "Wijaya"]
JALAN = ["Jl. Merdeka", "Jl. Kenanga", "Jl. Garuda", "Jl. Anggrek", "Jl. Mawar", "Jl. Cempaka",
         "Jl. Kenari", "Jl. Diponegoro", "Jl. Melati", "Jl. Elang", "Jl. Dahlia", "Jl. Flamboyan",
         "Jl. Bougenville", "Jl. Nusa Indah"]
RT = ["01", "02", "03", "04", "05", "06"]


def _nop(rnd: random.Random, kecamatan: str = "18", kelurahan: str = "010001", urut: int = 1,
         blok: int | None = None) -> str:
    """Bentuk singkat NOP seperti pada kolom 'TANGGAL BAYAR' file kelurahan: KK.KC.KEL.URUT-BLOK."""
    b = rnd.randint(0, 9) if blok is None else blok
    return f"35.{kecamatan}.{kelurahan}.{urut:04d}-{b}"


def generate_rows(n: int = 140, tahun: str = "2025", rnd: random.Random | None = None) -> list[dict]:
    rnd = rnd or random.Random(7)
    out = []
    used = set()
    i = 0
    while len(out) < n:
        i += 1
        blok_nama, _, _ = BLOK[len(out) % len(BLOK)]
        urut = 1 + i
        nop = _nop(rnd, kelurahan=f"010{1 + (i // 60):01d}001", urut=urut)
        if nop in used:
            continue
        used.add(nop)
        nama = f"{rnd.choice(DEPAN)} {rnd.choice(BELAKANG)}"
        jalan = rnd.choice(JALAN)
        alamat = f"{jalan} No. {rnd.randint(1, 120)} RT {rnd.choice(RT)}/RW {rnd.choice(RT)}"
        bumi = float(rnd.choice([60, 72, 84, 90, 96, 105, 110, 120, 144, 150, 168, 210, 240, 300]))
        bangunan = float(rnd.choice([0, 21, 36, 45, 54, 60, 70, 84, 96, 120]))
        njop = int(bumi * 18500 + bangunan * 96000)
        pbb = int(round(njop * 0.0009 / 100)) * 100 if njop > 10_000_000 else int(round(njop * 0.0005 / 100)) * 100
        pbb = max(4200, min(pbb, 480_000))
        out.append({
            "nop": nop, "tahun_pajak": tahun, "nama_wp": nama, "alamat_objek": alamat,
            "alamat_wp": alamat, "luas_bumi": bumi, "luas_bangunan": bangunan, "pbb": pbb,
            "denda": 0, "petugas_blok": blok_nama.split()[-1], "kelurahan": DESA.split(" ")[1],
            "kecamatan": KEC.split(" ")[1], "keterangan": "",
        })
    return out


def tulis_ketetapan(rows: list[dict], path: Path, sheet_judul: str = "DAFTAR KETETAPAN PBB-P2") -> Path:
    from openpyxl.styles import Font

    from core.excel import write_table

    wb = write_table(
        ["NOMOR", "NOP", "TAHUN PAJAK", "NAMA WAJIB PAJAK", "ALAMAT OBJEK PAJAK", "ALAMAT WAJIB PAJAK",
         "LUAS BUMI", "LUAS BANGUNAN", "PBB YG DIBAYAR", "NAMA PETUGAS BLOK", "KETERANGAN"],
        [[i + 1, r["nop"], r["tahun_pajak"], r["nama_wp"], r["alamat_objek"], r["alamat_wp"],
          r["luas_bumi"], r["luas_bangunan"], r["pbb"], r["petugas_blok"], ""]
         for i, r in enumerate(rows)],
        sheet_judul, f"{DESA} · {KEC} · {KAB}", money_cols=[9],
        widths=[7, 24, 11, 24, 32, 32, 10, 12, 15, 17, 14])
    ws = wb.active
    ws.cell(row=1, column=1).font = Font(bold=True, size=12, color="1F4E79")
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path


def tulis_realisasi(rows: list[dict], path: Path) -> Path:
    from core.excel import write_table

    wb = write_table(
        ["#", "NOP", "NAMA WP", "ALAMAT WP", "TAHUN PAJAK", "POKOK", "DENDA", "JUMLAH PEMBAYARAN",
         "TANGGAL PEMBAYARAN"],
        [[i + 1, r["nop"], r["nama_wp"], r["alamat_wp"], r["tahun_pajak"], r["pokok"], r["denda"],
          r["pokok"] + r["denda"], r["tgl_bayar"]] for i, r in enumerate(rows)],
        "REALISASI PAJAK DETAIL", "contoh hasil unduh /realisasi_rekap_lurah",
        money_cols=[6, 7, 8], widths=[5, 24, 22, 30, 11, 12, 10, 15, 16])
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path


def tulis_realisasi_csv(rows: list[dict], path: Path) -> Path:
    import csv

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(["#", "NOP", "NAMA WP", "ALAMAT WP", "TAHUN PAJAK", "POKOK", "DENDA",
                    "JUMLAH PEMBAYARAN", "TANGGAL PEMBAYARAN", "CHANNEL"])
        for i, r in enumerate(rows, 1):
            tgl = r["tgl_bayar"]
            tgl = f"{tgl[8:10]}/{tgl[5:7]}/{tgl[0:4]}" if len(tgl) == 10 else tgl
            w.writerow([i, r["nop"], r["nama_wp"], r["alamat_wp"], r["tahun_pajak"], r["pokok"],
                        r["denda"], r["pokok"] + r["denda"], tgl, r.get("channel", "Bank Jatim")])
    return path


def seed(bersihkan: bool = False) -> dict:
    import sys

    if str(BASE) not in sys.path:
        sys.path.insert(0, str(BASE))
    from werkzeug.security import generate_password_hash

    from core import services as S
    from core.db import connect, init_db, set_settings

    app_mod = __import__("app")
    app_mod.create()                     # memastikan skema + default settings tersedia
    init_db()
    rnd = random.Random(20250915)
    conn = connect()
    try:
        if bersihkan:
            conn.execute("DELETE FROM pembayaran")
            conn.execute("DELETE FROM realisasi")
            conn.execute("DELETE FROM billing_log")
            conn.execute("DELETE FROM import_batch")
            conn.execute("DELETE FROM tagihan")
            conn.execute("DELETE FROM petugas")
            conn.execute("DELETE FROM users WHERE username <> 'admin'")

        set_settings(conn, {
            "wilayah_desa": DESA, "wilayah_kecamatan": KEC.replace("Kecamatan ", ""),
            "wilayah_kabupaten": KAB,
            "alamat_kantor": "Jl. Raya Ketaman Ganggo Hilir No. 12, Telp. (0358) 000-000",
            "nama_lurah": "Drs. Hartono, M.Si", "nip_lurah": "197001011995011001",
            "no_rekening": "Bank Jatim 1234567890 a.n. Kas Desa/Kelurahan - via kode billing eSPPT",
        })
        conn.commit()
        stat = {"dihapus": int(bersihkan)}
        # --- petugas + akun login ---
        if conn.execute("SELECT COUNT(*) c FROM petugas").fetchone()["c"] == 0:
            for blok, nama, wil in BLOK:
                S.save_petugas(conn, {"nama": nama, "blok": blok, "wilayah": wil,
                                      "nip": f"197{rnd.randint(10, 99)}{rnd.randint(100000, 999999)}",
                                      "jabatan": f"Petugas Pemungut PBB-P2 {blok}", "aktif": True})
        petugas = S.daftar_petugas(conn, True)
        for p, (blok, nama, _w) in zip(petugas, BLOK):
            uname = "petugas" + blok.split()[-1]
            if not conn.execute("SELECT 1 FROM users WHERE username=?", (uname,)).fetchone():
                conn.execute("INSERT INTO users(username,password_hash,nama,role,petugas_id,created_at) "
                             "VALUES(?,?,?,?,?,?)",
                             (uname, generate_password_hash("pbb123"), nama, "petugas", p["id"],
                              dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit()

        # --- ketetapan ---
        tahun = str(dt.date.today().year)
        rows = generate_rows(140, tahun, rnd)
        folder = BASE / "contoh-file"
        folder.mkdir(exist_ok=True)
        f_ket = tulis_ketetapan(rows, folder / f"ketetapan-pbb-{tahun}.xlsx")
        dibaca, sheet = __import__("core.excel", fromlist=["x"]).read_sheet(f_ket, "ketetapan")
        stat["impor_ketetapan"] = S.impor_ketetapan(conn, dibaca, tahun, f_ket.name, "sistem", True)
        conn.commit()

        # --- pembayaran (sebagian lunas, sebagian baru diterima) ---
        tag = [dict(r) for r in conn.execute("SELECT * FROM tagihan WHERE tahun_pajak=?", (tahun,))]
        rnd.shuffle(tag)
        hari = dt.date.today()
        realisasi_rows = []
        dibuat = 0
        for i, t in enumerate(tag):
            if i % 5 in (0, 1, 2):
                metode = "petugas"
            elif i % 5 == 3:
                metode = "bank"
            else:
                continue
            try:
                res = S.catat_pembayaran(conn, t["id"], {
                    "jumlah": t["pokok"] + t["denda"],
                    "tgl_bayar": (hari - dt.timedelta(days=rnd.randint(0, 28))).isoformat(),
                    "metode": metode, "pembayar": t["nama_wp"], "catatan": ""},
                    t["petugas_id"], "sistem")
            except ValueError:
                continue
            dibuat += 1
            if metode == "petugas":
                if i % 5 == 0:
                    continue                      # sengaja masih di antrean billing
                conn.execute("UPDATE pembayaran SET status='terkirim', billing_code=?, tgl_billing=? "
                             "WHERE id=?", (f"{hari:%y%m%d}{1_000_000 + i}", res["tgl_bayar"], res["id"]))
            realisasi_rows.append({
                "nop": t["nop"], "nama_wp": t["nama_wp"], "alamat_wp": t["alamat_objek"],
                "tahun_pajak": tahun, "pokok": t["pokok"], "denda": t["denda"],
                "tgl_bayar": res["tgl_bayar"],
            })
        conn.commit()

        # --- WP yang bayar langsung ke bank (tanpa kwitansi petugas) ---
        for t in [x for x in tag if not conn.execute(
                "SELECT 1 FROM pembayaran b WHERE b.tagihan_id=? AND b.void=0", (x["id"],)).fetchone()][:14]:
            realisasi_rows.append({"nop": t["nop"], "nama_wp": t["nama_wp"], "alamat_wp": t["alamat_objek"],
                                   "tahun_pajak": tahun, "pokok": t["pokok"], "denda": t["denda"],
                                   "tgl_bayar": (hari - dt.timedelta(days=rnd.randint(0, 25))).isoformat()})

        f_real = tulis_realisasi(realisasi_rows, folder / f"realisasi-esppt-{tahun}.xlsx")
        # tulis juga versi CSV (banyak petugas mengolah dengan ; separator) untuk menguji parser CSV
        tulis_realisasi_csv(realisasi_rows, folder / f"realisasi-esppt-{tahun}.contoh.csv")
        from core.excel import read_sheet as _rs

        dibaca_real, _ = _rs(f_real, "realisasi")
        stat["impor_realisasi"] = S.impor_realisasi(conn, dibaca_real, f_real.name, "sistem")
        conn.commit()
        stat["pembayaran_dibuat"] = dibuat
        stat["file_ketetapan"] = str(f_ket)
        stat["file_realisasi"] = str(f_real)
        stat["petugas"] = [{"blok": p["blok"], "nama": p["nama"]} for p in petugas]
        stat["login"] = {"admin": "admin / admin123",
                         "petugas": "petugas1 … petugas7 / pbb123"}
        return stat
    finally:
        conn.close()


if __name__ == "__main__":
    import json
    import sys

    st = seed(bersihkan="--isi-ulang" in sys.argv)
    print(json.dumps(st, ensure_ascii=False, indent=2))
