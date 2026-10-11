"""Uji fungsional aplikasi — menjalankan kode asli lewat test client Flask.

Jalankan:  python -m pytest -q
"""
from __future__ import annotations

import datetime as dt
import io
import re
import sys
import time
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))


# --------------------------------------------------------------------- fixture

@pytest.fixture()
def app(tmp_path, monkeypatch):
    from core import db as dbmod

    monkeypatch.setattr(dbmod, "DATA_DIR", tmp_path)
    monkeypatch.setattr(dbmod, "DB_PATH", tmp_path / "pbb.db")
    monkeypatch.setattr(dbmod, "BACKUP_DIR", tmp_path / "backup")
    monkeypatch.setattr(dbmod, "UPLOAD_DIR", tmp_path / "upload")
    (tmp_path / "backup").mkdir()
    (tmp_path / "upload").mkdir()

    import app as appmod

    monkeypatch.setattr(appmod, "DATA_DIR", tmp_path)
    monkeypatch.setattr(appmod, "DB_PATH", tmp_path / "pbb.db")
    monkeypatch.setattr(appmod, "BACKUP_DIR", tmp_path / "backup")
    monkeypatch.setattr(appmod, "UPLOAD_DIR", tmp_path / "upload")

    flask_app = appmod.create()
    flask_app.config.update(TESTING=True)
    yield flask_app


@pytest.fixture()
def klien(app):
    return app.test_client()


def tag_seimbang(html: str) -> list[str]:
    """Kembalikan daftar div/table yang dibuka tapi tidak ditutup (kerusakan struktur)."""
    import re

    stack, rusak = [], []
    for m in re.finditer(r"<(/?)(div|table|form|main|article)(\s[^>]*)?>", html):
        tutup, nama = m.group(1) == "/", m.group(2)
        if tutup:
            if nama in stack:
                while stack and stack[-1] != nama:
                    rusak.append("tidak ditutup: " + stack.pop())
                stack.pop()
            else:
                rusak.append("penutup tanpa pembuka: " + nama)
        else:
            stack.append(nama)
    return rusak + ["tidak ditutup: " + x for x in stack]


@pytest.fixture()
def db_isolasi(tmp_path, monkeypatch):
    """Database kosong di folder sementara — untuk uji fungsi murni tanpa data contoh."""
    from core import db as dbmod

    monkeypatch.setattr(dbmod, "DATA_DIR", tmp_path)
    monkeypatch.setattr(dbmod, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(dbmod, "BACKUP_DIR", tmp_path / "backup")
    monkeypatch.setattr(dbmod, "UPLOAD_DIR", tmp_path / "upload")
    (tmp_path / "backup").mkdir()
    dbmod.init_db()
    yield
    dbmod.connect().close()


KOLOM_KETETAPAN = ["NOMOR", "NOP", "TAHUN PAJAK", "NAMA WAJIB PAJAK", "ALAMAT OBJEK PAJAK",
                   "ALAMAT WAJIB PAJAK", "LUAS BUMI", "LUAS BANGUNAN", "PBB YG DIBAYAR",
                   "NAMA PETUGAS BLOK"]
KOLOM_REALISASI = ["#", "NOP", "NAMA WP", "ALAMAT WP", "TAHUN PAJAK", "POKOK", "DENDA",
                   "JUMLAH PEMBAYARAN", "TANGGAL PEMBAYARAN"]


def xlsx_bytes(headers: list[str], rows: list[list]) -> bytes:
    from openpyxl import Workbook

    wb = Workbook()
    wb.active.append(headers)
    for r in rows:
        wb.active.append(r)
    bio = io.BytesIO()
    wb.save(bio)
    return bio.getvalue()


def token(klien, halaman="/billing") -> str:
    html = klien.get(halaman).get_data(as_text=True)
    m = re.search(r'name="_csrf" value="([^"]+)"', html)
    return m.group(1) if m else ""


def masuk(klien, user="admin", pw="admin123") -> None:
    """Login (selalu keluar dulu agar sesi bersih)."""
    klien.get("/logout")
    r = klien.post("/login", data={"_csrf": token(klien, "/login"), "username": user, "password": pw})
    assert r.status_code in (301, 302), r.get_data(as_text=True)[:300]


def post(klien, url, **data):
    d = {"_csrf": token(klien)}
    d.update(data)
    return klien.post(url, data=d, follow_redirects=True)


def impor(klien, headers, rows, nama_file, tahun="2026", jenis="ketetapan"):
    return klien.post("/impor", data={
        "_csrf": token(klien), "jenis": jenis, "tahun": tahun,
        "file": (io.BytesIO(xlsx_bytes(headers, rows)), nama_file)},
        content_type="multipart/form-data", follow_redirects=True)


def empat_rows():
    return [[i + 1, f"35.18.010000{i}.000{i + 1}.{i + 2:02d}-0", "2026", f"Wajib Pajak {i + 1}",
             f"Jl. Uji {i + 1}", f"Jl. Uji {i + 1}", 100 + i * 5, 40 + i, 30000 + i * 1500,
             "Blok 1"] for i in range(4)]


def bayar(klien, tag_id, jumlah="30000", metode="petugas", tgl="2026-09-15"):
    return post(klien, f"/bayar/{tag_id}", tgl_bayar=tgl, jumlah=jumlah, metode=metode)


def conn():
    from core.db import connect

    return connect()


# ------------------------------------------------------------------- format

def test_format_dasar():
    from core import format as F

    assert F.rp(1234567) == "Rp1.234.567"
    assert F.terbilang(285000) == "dua ratus delapan puluh lima ribu rupiah"
    assert F.terbilang(1_100_000) == "satu juta seratus ribu rupiah"
    assert F.terbilang(15) == "lima belas rupiah"
    assert F.to_date("29/08/2026") == dt.date(2026, 8, 29)
    assert F.to_date(dt.date(2026, 1, 5)) == dt.date(2026, 1, 5)
    assert F.persen(25, 100) == 25 and F.persen(1, 0) == 0


def test_norm_nop():
    from core import format as F

    pendek = F.norm_nop("35.18.010001.0001-0")            # bentuk warisan 17 digit
    assert pendek and F.nop_valid(pendek)
    assert F.nop_valid(F.norm_nop("35.18.0100000.0001.02-0"))
    assert F.norm_nop("351801000000001002") == "35.18.0100000.0001.00-2"
    assert F.norm_nop("bukan nop") == "" and F.norm_nop(None) == "" and F.norm_nop("12345") == ""
    # dua penulisan untuk objek yang sama harus menghasilkan kunci yang sama
    assert F.norm_nop("35.18.010001.0001-0") == F.norm_nop("35.18.010001.0001-0")


def test_esppt_parse_curl_dan_placeholder():
    from core.esppt import parse_curl

    cmd = ("curl 'https://esppt.nganjukkab.go.id/sppt/tagihan/billing' "
           "-X POST -H 'Content-Type: application/x-www-form-urlencoded' "
           "--data-raw 'nop={nop}&tahun={tahun}&denda={denda}' --compressed")
    s = parse_curl(cmd)
    assert s.method == "POST" and s.url.endswith("/sppt/tagihan/billing")
    f = s.filled({"nop": "35.18.0100000.0001.02-0", "tahun": "2026", "denda": 0})
    assert "nop=35.18.0100000.0001.02-0" in f.body and "tahun=2026" in f.body

    from core.esppt import EspptClient

    assert EspptClient._extract("billing code: <b>2812026000123456</b>",
                               r"billing code:\s*<b>(\d+)</b>") == "2812026000123456"
    assert EspptClient._extract("kode: 2812026000123456", r"(?:[0-9]{12,20})") == "2812026000123456"


def test_esppt_parse_realisasi_html_dan_json():
    from core.esppt import EspptClient, Resp

    html = """<table>
      <tr><th>#</th><th>NOP</th><th>NAMA WP</th><th>ALAMAT WP</th><th>TAHUN PAJAK</th>
      <th>POKOK</th><th>DENDA</th><th>JUMLAH PEMBAYARAN</th><th>TANGGAL PEMBAYARAN</th></tr>
      <tr><td>1</td><td>35.18.0100000.0001.02-0</td><td>Slamet</td><td>Jl. Merdeka 1</td>
      <td>2026</td><td>28,500</td><td>0</td><td>28,500</td><td>20/09/2026</td></tr>
      <tr><td>2</td><td>35.18.0100000.0002.01-0</td><td>Siti</td><td>Jl. Kenanga 7</td>
      <td>2026</td><td>41,250</td><td>1,000</td><td>42,250</td><td>21/09/2026</td></tr></table>"""
    rows = EspptClient.parse_realisasi(Resp(True, 200, html))
    assert len(rows) == 2, rows
    assert rows[0]["jumlah"] == 28500 and rows[0]["tgl_bayar"] == "20/09/2026"
    assert rows[1]["pokok"] == 41250 and rows[1]["denda"] == 1000

    js = ('{"data":[{"NOP":"35.18.0100000.0001.02-0","NAMA_WP":"X","POKOK":28500,'
          '"JUMLAH_PEMBAYARAN":28500,"TANGGAL_PEMBAYARAN":"2026-09-20","TAHUN_PAJAK":2026}]}')
    r2 = EspptClient.parse_realisasi(Resp(True, 200, js))
    assert len(r2) == 1 and r2[0]["jumlah"] == 28500 and r2[0]["tahun_pajak"] == 2026


def test_esppt_dry_run_dan_setting_mati():
    """Pengaman penting: selama otomatisasi mati/dry-run, TIDAK ADA request keluar."""
    from core.esppt import EspptClient, Resp

    panggil = []
    asli = EspptClient.http

    def rekam(self, url, *a, **k):
        panggil.append(url)
        return Resp(True, 200, "")

    EspptClient.http = rekam
    try:
        mati = EspptClient({"esppt_enabled": "0", "esppt_billing_dry_run": "1"})
        r = mati.buat_billing("35.18.0100000.0001.02-0", "2026")
        assert not r.ok and "Template request billing belum diisi" in r.error

        hidup = EspptClient({"esppt_enabled": "1", "esppt_billing_dry_run": "1",
                             "esppt_billing_template": "curl 'https://x.test/cek' -X POST "
                             "--data-raw 'nop={nop}' -H 'Content-Type: application/x-www-form-urlencoded'"})
        r2 = hidup.buat_billing("35.18.0100000.0001.02-0", "2026")
        assert not r2.ok and "Dry-run aktif" in r2.error
        assert panggil == []               # tidak ada satu pun request dikirim
    finally:
        EspptClient.http = asli


# ------------------------------------------------------------------ impor excel

def test_template_curl_dipakai_untuk_realisasi_dan_billing(tmp_path, db_isolasi):
    """Template 'Copy as cURL' harus diurai (bukan dianggap URL mentah) & placeholder terisi."""
    from core.esppt import EspptClient, Resp

    dipanggil = []

    def palsu(self, url, data=None, method="GET", headers=None, raw_body=False):
        dipanggil.append({"url": url, "data": data, "method": method, "raw": raw_body})
        return Resp(True, 200, '{"data":[{"NOP":"35.18.0100000.0001.02-0","TAHUN_PAJAK":2026,'
                              '"JUMLAH_PEMBAYARAN":28500,"TANGGAL_PEMBAYARAN":"2026-09-20"}]}')

    s = {
        "esppt_enabled": "1", "esppt_billing_dry_run": "0", "esppt_base_url": "https://esppt.test",
        "esppt_realisasi_template": "curl 'https://esppt.test/lap/export?tahun={tahun}' "
                                    "-H 'X-Requested-With: XMLHttpRequest'",
        "esppt_billing_template": "curl 'https://esppt.test/tagihan/billing' -X POST "
                                  "-H 'Content-Type: application/x-www-form-urlencoded' "
                                  "--data-raw 'nop={nop}&tahun={tahun}'",
        "esppt_billing_regex": '"([0-9]{12,20})"',
    }
    asli = EspptClient.http
    EspptClient.http = palsu
    try:
        cli = EspptClient(s)
        r = cli.ambil_realisasi("2026")
        assert r.ok and dipanggil[-1]["url"].endswith("tahun=2026")
        rows = EspptClient.parse_realisasi(r)
        assert rows and rows[0]["nop"] == "35.18.0100000.0001.02-0" and rows[0]["jumlah"] == 28500

        cli2 = EspptClient(s)

        def palsu2(self, url, data=None, method="POST", headers=None, raw_body=False):
            dipanggil.append({"url": url, "data": data, "method": method})
            return Resp(True, 200, '{"sukses":true,"kode billing": "2812026000123456"}')

        EspptClient.http = palsu2
        b = cli2.buat_billing("35.18.0100000.0001.02-0", "2026", 0)
        assert b.ok and b.text == "2812026000123456", b
        assert "nop=35.18.0100000.0001.02-0" in dipanggil[-1]["data"]
    finally:
        EspptClient.http = asli


def test_folder_auto_menelan_file(tmp_path, monkeypatch, db_isolasi):
    """File yang diletakkan di data/auto/ harus terimpor sendiri lalu dipindah (tidak dobel)."""
    from core import services as S
    from core.db import connect
    from core.excel import save, write_table

    folder = tmp_path / "auto"
    (folder / "sudah-diproses").mkdir(parents=True)
    monkeypatch.setattr(S, "DATA_AUTO_DIR", folder)
    save(write_table(KOLOM_KETETAPAN,
                          [[1, "35.18.0100000.0001.02-0", "2026", "Slamet", "Jl. A", "Jl. A",
                            100, 40, 28500, "Blok 1"]], "x"), folder / "ketetapan-2026.xlsx")
    f2 = save(write_table(KOLOM_REALISASI,
                          [["1", "35.18.0100000.0001.02-0", "Slamet", "Jl. A", "2026", 28500, 0,
                            28500, "20/09/2026"]], "y"), folder / "realisasi-minggu-35.xlsx")
    c = connect()
    try:
        hasil = S.proses_folder_auto(c)
        assert len(hasil) == 2, hasil
        assert all(h.get("ok") for h in hasil), hasil
        assert sorted(h["jenis"] for h in hasil) == ["ketetapan", "realisasi"]
        assert not list(folder.glob("*.xlsx")), "file harus dipindah dari folder auto"
        b = c.execute("SELECT status FROM pembayaran JOIN tagihan t ON t.id=tagihan_id").fetchone()
        assert b["status"] == "terverifikasi"          # realisasi langsung menandai lunas
        assert S.proses_folder_auto(c) == []           # tidak ada pekerjaan tersisa
        f2.unlink(missing_ok=True)
        (folder / "rusak.xlsx").write_bytes(b"bukan excel")
        hasil2 = S.proses_folder_auto(c)
        assert hasil2 and hasil2[0]["ok"] is False and "GAGAL" in "".join(
            p.name for p in (folder / "sudah-diproses").iterdir())
    finally:
        c.close()


def test_impor_ketetapan_toleran_variasi(tmp_path, db_isolasi):
    from core import services as S
    from core.excel import read_sheet, save, write_table

    rows = [[1, "35.18.0100000.0001.02-0", "2026", "Slamet Wibowo", "Jl. Merdeka 1", "Jl. Merdeka 1",
             210, 60, 28500, "Blok 1"],
            [2, "35.18.0100000.0002.01-0", "2026", "Siti Aminah", "Jl. Kenanga 7", "Jl. Kenanga 7",
             96, 36, 41250, "Blok 2"]]
    path = save(write_table(KOLOM_KETETAPAN, rows, "DAFTAR KETETAPAN PBB-P2",
                           "Kelurahan ... · Kecamatan ...", money_cols=[9], widths=[8] * 10),
                tmp_path / "ketetapan.xlsx")
    dibaca, sheet = read_sheet(path, "ketetapan")
    assert len(dibaca) == 2, dibaca
    assert dibaca[0]["nama_wp"] == "Slamet Wibowo" and dibaca[0]["pbb"] == 28500

    c = conn()
    try:
        stat = S.impor_ketetapan(c, dibaca, "2026", path.name, "tester", False)
        c.commit()
        assert stat["baru"] == 2 and stat["nop_salah"] == 0
        t = c.execute("SELECT * FROM tagihan WHERE nama_wp='Slamet Wibowo'").fetchone()
        p = c.execute("SELECT * FROM petugas WHERE blok='Blok 1'").fetchone()
        assert t["pokok"] == 28500 and t["petugas_id"] == p["id"] and t["nop"] == "35.18.0100000.0001.02-0"

        rows[0][8] = 31000                                  # nominal diperbarui
        p2 = save(write_table(KOLOM_KETETAPAN, rows, "x"), tmp_path / "k2.xlsx")
        dibaca2, _ = read_sheet(p2, "ketetapan")
        stat2 = S.impor_ketetapan(c, dibaca2, "2026", "k2.xlsx", "tester", False)
        c.commit()
        assert stat2["ubah"] == 1 and stat2["baru"] == 0
        assert c.execute("SELECT pokok FROM tagihan WHERE id=?", (t["id"],)).fetchone()["pokok"] == 31000
        # impor ulang file yang sama: tidak ada perubahan sama sekali
        stat3 = S.impor_ketetapan(c, dibaca2, "2026", "k2.xlsx", "tester", False)
        assert stat3["sama"] == 2 and stat3["baru"] == 0
    finally:
        c.close()


def test_petugas_blok_dipetakan_dan_dibuat_otomatis(tmp_path, db_isolasi):
    from core import services as S

    from core.excel import read_sheet, save, write_table

    c = conn()
    try:
        cache: dict = {}
        id1 = S.resolve_petugas(c, "1", cache)
        id2 = S.resolve_petugas(c, "Blok 1", cache)
        assert id1 == id2                                      # "1" dan "Blok 1" = orang yang sama
        id3 = S.resolve_petugas(c, "BLOK-3", cache)
        assert id3 and id3 != id1
        c.commit()
        rows = [[1, "35.18.0100000.0007.03-0", "2026", "Uji Blok", "Jl. A", "Jl. A", 100, 36,
                 5000, "blok baru yang belum dikenal"]]
        p = save(write_table(KOLOM_KETETAPAN, rows, "x"), tmp_path / "b.xlsx")
        dibaca, _ = read_sheet(p, "ketetapan")
        stat = S.impor_ketetapan(c, dibaca, "2026", "b.xlsx", "tester", False)
        c.commit()
        assert stat["baru"] == 1
        t = c.execute("SELECT p.blok FROM tagihan t LEFT JOIN petugas p ON p.id=t.petugas_id "
                      "WHERE t.nama_wp='Uji Blok'").fetchone()
        assert t["blok"] == "blok baru yang belum dikenal"[:24]
    finally:
        c.close()


# -------------------------------------------------------- alur ujung ke ujung

def test_alur_lengkap_dari_impor_sampai_lunas(klien, app):
    masuk(klien)
    assert "Impor ketetapan selesai" in impor(klien, KOLOM_KETETAPAN, empat_rows(), "k.xlsx").get_data(as_text=True)

    c = conn()
    tag = c.execute("SELECT * FROM tagihan ORDER BY id LIMIT 1").fetchone()
    c.close()
    assert tag["pokok"] == 30000

    r = bayar(klien, tag["id"])
    body = r.get_data(as_text=True)
    assert r.status_code == 200 and "KWITANSI" in body.upper()
    nomor = re.search(r"Nomor:\s*(\d{2}/2026/\d{3})", body).group(1)
    assert "tiga puluh ribu rupiah" in body.lower() and tag["nop"] in body

    # NOP sama tidak boleh dibayar dua kali tanpa membatalkan
    assert "sudah tercatat" in bayar(klien, tag["id"]).get_data(as_text=True)

    c = conn()
    b = c.execute("SELECT * FROM pembayaran WHERE nomor_kwitansi=?", (nomor,)).fetchone()
    pid = b["id"]
    assert b["status"] == "menunggu_billing" and b["jumlah_diterima"] == 30000
    c.close()

    r = klien.get(f"/kwitansi/{pid}.pdf")
    assert r.status_code == 200 and r.data[:4] == b"%PDF"
    pymupdf = pytest.importorskip("pymupdf", reason="opsional: perlu pymupdf utk membaca PDF")

    dok = pymupdf.open(stream=r.data, filetype="pdf")
    assert dok.page_count == 1 and nomor in dok[0].get_text()
    assert "35.18.0100000.0001" in dok[0].get_text()

    # nomor urut berikutnya maju, bukan didaur ulang
    c = conn()
    t2 = c.execute("SELECT id FROM tagihan ORDER BY id LIMIT 1 OFFSET 1").fetchone()["id"]
    c.close()
    bayar(klien, t2, jumlah="31500")
    c = conn()
    b2 = c.execute("SELECT nomor_kwitansi FROM pembayaran WHERE tagihan_id=?", (t2,)).fetchone()
    c.close()
    assert b2 and b2["nomor_kwitansi"].endswith("002"), b2

    assert tag["nop"] in klien.get("/billing").get_data(as_text=True)
    r = post(klien, "/billing/tempel", tempel=f"{tag['nop']} ; 2812026000123456")
    assert "tercatat untuk 1 NOP" in r.get_data(as_text=True)
    c = conn()
    row = c.execute("SELECT billing_code, status FROM pembayaran WHERE id=?", (pid,)).fetchone()
    c.close()
    assert row["billing_code"] == "2812026000123456" and row["status"] == "terkirim"

    c = conn()
    nop2 = c.execute("SELECT nop FROM tagihan WHERE id=?", (t2,)).fetchone()["nop"]
    c.close()
    r = impor(klien, KOLOM_REALISASI,
              [["1", tag["nop"], "Wajib Pajak 1", "Jl. Uji 1", "2026", 30000, 0, 30000, "20/09/2026"],
               ["2", nop2, "Wajib Pajak 2", "Jl. Uji 2", "2026", 31500, 0, 31500, "20/09/2026"]],
              "r.xlsx", jenis="realisasi")
    assert r.status_code == 200
    c = conn()
    st = c.execute("SELECT status, tgl_verifikasi FROM pembayaran WHERE id=?", (pid,)).fetchone()
    c.close()
    assert st["status"] == "terverifikasi" and st["tgl_verifikasi"]

    html = klien.get("/laporan?tahun=2026").get_data(as_text=True)
    assert "Blok 1" in html and "50%" in html          # 1 lunas dari 4 SPPT blok 1


def test_realisasi_menandai_bayar_mandiri(klien, app):
    masuk(klien)
    impor(klien, KOLOM_KETETAPAN, empat_rows(), "k.xlsx")
    c = conn()
    t3 = c.execute("SELECT * FROM tagihan ORDER BY id LIMIT 1 OFFSET 2").fetchone()
    c.close()
    # tidak ada kwitansi sama sekali; hanya muncul di realisasi (WP bayar langsung ke bank)
    impor(klien, KOLOM_REALISASI, [["1", t3["nop"], t3["nama_wp"], t3["alamat_objek"], "2026",
                                    t3["pokok"], 0, t3["pokok"], "21/09/2026"]], "r.xlsx",
          jenis="realisasi")
    from core import services as S

    c = conn()
    b = c.execute("SELECT metode, status, nomor_kwitansi FROM pembayaran WHERE tagihan_id=?",
                  (t3["id"],)).fetchone()
    lap = {r["blok"]: r for r in S.laporan_blok(c, "2026")}
    c.close()
    assert b["metode"] == "bank" and b["status"] == "terverifikasi" and b["nomor_kwitansi"] is None
    assert lap["Blok 1"]["bayar_sendiri"] == 1 and lap["Blok 1"]["lunas_sppt"] == 1
    assert lap["Blok 1"]["via_petugas"] == 0            # belum ada setoran petugas sama sekali


def test_selisih_kwitansi_dan_setoran_tertahan(klien, app):
    masuk(klien)
    impor(klien, KOLOM_KETETAPAN, empat_rows(), "k.xlsx")
    c = conn()
    tid = c.execute("SELECT id FROM tagihan ORDER BY id LIMIT 1").fetchone()["id"]
    c.close()
    bayar(klien, tid)
    from core import services as S

    c = conn()
    sel = S.selisih_kwitansi_vs_setoran(c, "2026")
    setor = S.rekap_kwitansi(c, "2026", None, "", "")
    c.close()
    assert len(setor) == 1 and setor[0]["nomor_kwitansi"].endswith("/2026/001") and setor[0]["blok"] == "Blok 1", setor
    assert len(sel) == 1 and sel[0]["status"] == "menunggu_billing"
    html = klien.get("/laporan/setoran-ke-kas?tahun=2026").get_data(as_text=True)
    assert "Blok 1" in html and "Rp30.000" in html


def test_jalan_pintas_cari_nop(klien, app):
    masuk(klien)
    impor(klien, KOLOM_KETETAPAN, empat_rows(), "k.xlsx")
    c = conn()
    satu = c.execute("SELECT id, nop FROM tagihan ORDER BY id LIMIT 1").fetchone()
    c.close()
    nop, tid = satu["nop"], satu["id"]

    r = klien.get(f"/cari?nop={nop}")                       # 1 kecocokan -> dilempar ke formulir bayar
    assert r.status_code == 302 and f"/bayar/{tid}" in r.headers["Location"]
    r = klien.get(f"/cari?nop={nop}", follow_redirects=True)
    assert "Rekam Pembayaran" in r.get_data(as_text=True)
    assert klien.get("/cari?nop=351801000000001020").status_code in (200, 302)   # NOP tanpa titik

    r = klien.get("/cari?nop=Wajib")                        # banyak kecocokan -> daftar pilihan
    assert r.status_code == 200 and "NOP cocok" in r.get_data(as_text=True)
    assert klien.get("/cari").status_code == 302            # kosong -> ke daftar data
    r = klien.get("/cari?nop=35.99.0100000.0009.09-0", follow_redirects=True)
    assert "tidak ada dalam data ketetapan" in r.get_data(as_text=True)

    bayar(klien, tid)
    r = klien.get(f"/cari?nop={nop}", follow_redirects=True)   # sudah ada kwitansi -> langsung kwitansi
    assert "KWITANSI" in r.get_data(as_text=True).upper()


def test_semua_halaman_admin_terbuka(klien, app):
    masuk(klien)
    impor(klien, KOLOM_KETETAPAN, empat_rows(), "k.xlsx")
    c = conn()
    tid = c.execute("SELECT id FROM tagihan ORDER BY id LIMIT 1").fetchone()["id"]
    c.close()
    bayar(klien, tid)
    for url in ("/", "/data?tahun=2026", f"/data/detail/{tid}", "/transaksi", "/billing", "/realisasi",
                "/laporan?tahun=2026", "/laporan/kwitansi?tahun=2026", "/laporan/setoran-ke-kas?tahun=2026",
                "/monitor", "/petugas", "/pengaturan", "/pengaturan?tab=esppt", "/impor",
                "/unduh/format?jenis=ketetapan", "/unduh/format?jenis=realisasi",
                "/laporan/progres.xlsx?tahun=2026", "/laporan/realisasi.csv?tahun=2026",
                "/cari?nop=Wajib", "/data?cari=35.18&status=belum",
                "/data?status=proses", "/data?petugas=1"):
        r = klien.get(url)
        assert r.status_code == 200, f"{url} -> {r.status_code}"
        if "html" in r.mimetype:
            assert tag_seimbang(r.get_data(as_text=True)) == [], f"struktur HTML {url} rusak"
    assert klien.get("/laporan/progres.xlsx?tahun=2026").data[:2] == b"PK"


def test_kwitansi_pdf_semua_metode(klien, app):
    masuk(klien)
    impor(klien, KOLOM_KETETAPAN, empat_rows(), "k.xlsx")
    c = conn()
    ids = [r["id"] for r in c.execute("SELECT id FROM tagihan ORDER BY id LIMIT 4")]
    c.close()
    for i, tid in enumerate(ids):
        bayar(klien, tid, metode=["petugas", "bank", "pos", "wp"][i], tgl=f"2026-09-{10 + i}")
    c = conn()
    pids = [r["id"] for r in c.execute("SELECT id FROM pembayaran ORDER BY id")]
    c.close()
    pymupdf = pytest.importorskip("pymupdf", reason="opsional: perlu pymupdf utk membaca PDF")

    assert len(pids) == 4
    for pid in pids:
        r = klien.get(f"/kwitansi/{pid}.pdf")
        assert r.status_code == 200 and r.data[:4] == b"%PDF", pid
        assert pymupdf.open(stream=r.data, filetype="pdf").page_count == 1
        assert klien.get(f"/kwitansi/{pid}").status_code == 200


def test_cek_kwitansi_publik(klien, app):
    masuk(klien)
    impor(klien, KOLOM_KETETAPAN, empat_rows(), "k.xlsx")
    c = conn()
    tid = c.execute("SELECT id FROM tagihan ORDER BY id LIMIT 1").fetchone()["id"]
    c.close()
    bayar(klien, tid)
    c = conn()
    nomor = c.execute("SELECT nomor_kwitansi FROM pembayaran ORDER BY id DESC LIMIT 1").fetchone()["nomor_kwitansi"]
    c.close()
    klien.get("/logout")
    r = klien.get(f"/cek/{nomor}")
    assert r.status_code == 200 and "sah" in r.get_data(as_text=True)
    assert klien.get("/cek/TIDAK-ADA-999").status_code == 404


def test_impor_baris_nop_rusak_dilaporkan(klien, app):
    masuk(klien)
    r = impor(klien, KOLOM_KETETAPAN,
              [[1, "35.18.0100000.0001.02-0", "2026", "Baik", "Jl. A", "Jl. A", 100, 40, 30000, "Blok 1"],
               [2, "abc", "2026", "Rusak", "Jl. B", "Jl. B", 100, 40, 30000, "Blok 1"]], "rusak.xlsx")
    assert "dilewati karena NOP tidak terbaca" in r.get_data(as_text=True)


def test_billing_ditolak_saat_otomatisasi_mati(klien, app):
    masuk(klien)
    impor(klien, KOLOM_KETETAPAN, empat_rows(), "k.xlsx")
    c = conn()
    tid = c.execute("SELECT id FROM tagihan ORDER BY id LIMIT 1").fetchone()["id"]
    c.close()
    bayar(klien, tid)
    c = conn()
    pid = c.execute("SELECT MAX(id) m FROM pembayaran").fetchone()["m"]
    c.close()
    assert "Otomatisasi eSPPT masih MATI" in post(klien, "/billing/buat", id=pid).get_data(as_text=True)

    assert klien.post("/api/sinkron/start", data={"_csrf": token(klien), "billing": "1"}).status_code == 200
    st = {}
    for _ in range(60):
        st = klien.get("/api/sinkron/status").get_json()
        if not st["berjalan"]:
            break
        time.sleep(0.1)
    assert st["berjalan"] is False
    assert "belum diaktifkan" in " ".join(l["msg"] for l in st["log"]).lower()


def test_batal_kwitansi_nomor_tidak_daur_ulang(klien, app):
    masuk(klien)
    impor(klien, KOLOM_KETETAPAN, empat_rows(), "k.xlsx")
    c = conn()
    tid = c.execute("SELECT id FROM tagihan ORDER BY id LIMIT 1").fetchone()["id"]
    c.close()
    bayar(klien, tid)
    c = conn()
    b = c.execute("SELECT * FROM pembayaran ORDER BY id DESC LIMIT 1").fetchone()
    c.close()
    post(klien, f"/transaksi/batal/{b['id']}")
    c = conn()
    ada = c.execute("SELECT void FROM pembayaran WHERE id=?", (b["id"],)).fetchone()
    c.close()
    assert ada["void"] == 1                                  # tidak dihapus, hanya dibatalkan
    r = bayar(klien, tid, tgl="2026-09-16")
    nomor_baru = re.search(r"Nomor:\s*(\d{2}/2026/\d{3})", r.get_data(as_text=True)).group(1)
    assert nomor_baru != b["nomor_kwitansi"]


def test_petugas_hanya_blok_sendiri(klien, app):
    masuk(klien)
    impor(klien, KOLOM_KETETAPAN, empat_rows(), "k.xlsx")
    post(klien, "/petugas/simpan", nama="Coba Uji", blok="Blok 9", aktif="1",
         buat_user="1", password="rahasia1")
    c = conn()
    u = c.execute("SELECT * FROM users WHERE username='coba-uji'").fetchone()
    pid = c.execute("SELECT id FROM petugas WHERE blok='Blok 9'").fetchone()["id"]
    punya = c.execute("SELECT id, petugas_id FROM tagihan ORDER BY id LIMIT 1").fetchone()
    punya_blok9 = c.execute("SELECT id FROM tagihan WHERE petugas_id=? LIMIT 1", (pid,)).fetchone()
    c.close()
    assert u and u["role"] == "petugas" and u["petugas_id"] == pid
    assert punya_blok9 is None                    # blok baru belum punya satu NOP pun
    masuk(klien, "coba-uji", "rahasia1")
    for halaman in ("/laporan", "/impor", "/pengaturan", "/petugas", "/realisasi"):
        assert klien.get(halaman).status_code == 403, halaman
    r = klien.get("/")
    assert r.status_code == 302 and "/data" in r.headers["Location"]   # petugas -> daftar ketetapan
    if punya:                                                # NOP milik petugas lain -> ditolak
        assert klien.get(f"/bayar/{punya['id']}").status_code == 403
    html = klien.get("/data?tahun=2026").get_data(as_text=True)
    assert "Blok 1" not in html or "belum dipetakan" in html          # petugas tak melihat NOP blok lain
    assert "Coba Uji" in html                               # petugas melihat bloknya sendiri


def test_csv_dan_tolak_password_salah(klien, app):
    with pytest.raises(AssertionError):
        masuk(klien, "admin", "salah-sekali")
    masuk(klien)
    impor(klien, KOLOM_KETETAPAN, empat_rows(), "k.xlsx")
    c = conn()
    tid = c.execute("SELECT id FROM tagihan ORDER BY id LIMIT 1").fetchone()["id"]
    c.close()
    bayar(klien, tid)
    r = klien.get("/transaksi?tahun=2026&format=csv")
    assert r.status_code == 200 and b"nomor_kwitansi" in r.data and b";" in r.data
    assert b"nop" in r.data
    r = klien.get("/laporan/kwitansi?tahun=2026&format=csv")
    assert r.status_code == 200 and b"nomor_kwitansi" in r.data


def test_token_csrf_diperlukan(klien, app):
    masuk(klien)
    r = klien.post("/petugas/simpan", data={"nama": "Tanpa Token", "blok": "Blok X"})
    assert r.status_code == 400


def test_pengaturan_esppt_tersimpan_dan_dipakai(klien, app):
    masuk(klien)
    r = post(klien, "/pengaturan", grup="esppt", esppt_enabled="1", esppt_base_url="https://x.test",
             esppt_user="petugas1", esppt_password="rahasia", esppt_http_timeout="5",
             esppt_delay_ms="0", esppt_billing_dry_run="1", esppt_realisasi_path="/realisasi",
             esppt_realisasi_params='{"tahun":"{tahun}"}')
    assert r.status_code == 200
    c = conn()
    from core.db import get_settings

    s = get_settings(c)
    c.close()
    assert s["esppt_enabled"] == "1" and s["esppt_base_url"] == "https://x.test"
    assert s["esppt_user"] == "petugas1"
    html = klien.get("/pengaturan?tab=esppt").get_data(as_text=True)
    assert 'value="https://x.test"' in html
    assert re.search(r'name="esppt_enabled"[^>]*checked', html), "checkbox aktif harus tetap tercentang"
    assert 'value="5"' in html and 'name="esppt_password"' in html

    # uji koneksi ke host palsu -> harus menampilkan pesan kegagalan, bukan crash
    r = post(klien, "/pengaturan/uji", langkah="koneksi")
    assert r.status_code == 200 and "Koneksi ke server eSPPT" in r.get_data(as_text=True)


def test_gaya_halaman_disisipkan_langsung(klien, app):
    """Style ikut di dalam HTML (bukan hanya <link>) supaya halaman tetap rapi walau berkas
    static/ tidak sempat dimuat — mis. saat ditinjau di pratinjau berkas."""
    masuk(klien)
    html = klien.get("/").get_data(as_text=True)
    assert "<style>" in html and ".topbar" in html


# --------------------------------------------- denda otomatis, sortir, pindah blok

def test_hitung_denda_per_bulan(db_isolasi):
    from core import services as S
    from core.db import get_settings

    c = conn()
    s = get_settings(c)
    assert s["jatuh_tempo_bulan"] == "8" and s["denda_persen"] == "1" and s["denda_maks_bulan"] == "24"
    assert S.hitung_denda(100000, "2026", s, hari=dt.date(2026, 8, 31)) == 0       # bulan jatuh tempo
    assert S.hitung_denda(100000, "2026", s, hari=dt.date(2026, 9, 1)) == 1000    # 1 bulan
    assert S.hitung_denda(100000, "2026", s, hari=dt.date(2026, 10, 20)) == 2000  # 2 bulan
    assert S.hitung_denda(100000, "2024", s, hari=dt.date(2026, 10, 20)) == 24000  # mentok 24 bulan
    assert S.hitung_denda(100000, "2027", s, hari=dt.date(2026, 10, 20)) == 0     # tahun depan
    assert S.hitung_denda(0, "2026", s, hari=dt.date(2026, 10, 20)) == 0
    c.close()


def test_denda_skenario_sppt_jatuh_tempo_31_juli(db_isolasi):
    """Skenario ketetapan daerah: SPPT 2027 jatuh tempo 31 Juli, denda 1%/bulan.

    1 Agustus NOP yang belum bayar muncul denda 1%; 1 September menjadi 2%;
    dalam bulan yang sama jumlahnya tidak bertambah; setelah dibayar denda
    berhenti menambah (dibekukan -- diuji terpisah di test_perbarui_denda)."""
    from core import services as S
    from core.db import get_settings

    c = conn()
    s = get_settings(c)
    s["jatuh_tempo_bulan"] = "7"
    s["denda_persen"] = "1"
    assert S.hitung_denda(500000, "2027", s, hari=dt.date(2027, 7, 31)) == 0      # hari jatuh tempo
    assert S.hitung_denda(500000, "2027", s, hari=dt.date(2027, 8, 1)) == 5000    # 1 Agustus: 1%
    assert S.hitung_denda(500000, "2027", s, hari=dt.date(2027, 8, 31)) == 5000   # sepanjang Agustus 1%
    assert S.hitung_denda(500000, "2027", s, hari=dt.date(2027, 9, 1)) == 10000   # 1 September: 2%
    assert S.hitung_denda(500000, "2027", s, hari=dt.date(2027, 9, 30)) == 10000  # sepanjang September 2%
    assert S.hitung_denda(500000, "2027", s, hari=dt.date(2028, 7, 31)) == 60000  # 12 bulan x 1%
    c.close()


def test_perbarui_denda_beku_untuk_yang_sudah_dibayar(db_isolasi):
    from core import services as S

    c = conn()
    c.execute("INSERT INTO tagihan(tahun_pajak,nop,nama_wp,pokok,denda,aktif,created_at,updated_at) "
              "VALUES('2026','35.18.0100000.0001.001-0','WP A',100000,0,1,'2026-01-01','2026-01-01')")
    c.execute("INSERT INTO tagihan(tahun_pajak,nop,nama_wp,pokok,denda,aktif,created_at,updated_at) "
              "VALUES('2026','35.18.0100000.0002.001-0','WP B',100000,0,1,'2026-01-01','2026-01-01')")
    tid_a = c.execute("SELECT id FROM tagihan WHERE nop='35.18.0100000.0001.001-0'").fetchone()["id"]
    c.execute("INSERT INTO pembayaran(tagihan_id,metode,jumlah_diterima,status,void,created_at,updated_at) "
              "VALUES(?,'petugas',50000,'menunggu_billing',0,'2026-01-01','2026-01-01')", (tid_a,))
    c.commit()
    n = S.perbarui_denda(c, hari=dt.date(2026, 10, 20))     # JT Agustus -> 2 bulan terlampaui
    c.commit()
    d_a = c.execute("SELECT denda FROM tagihan WHERE nop='35.18.0100000.0001.001-0'").fetchone()["denda"]
    d_b = c.execute("SELECT denda FROM tagihan WHERE nop='35.18.0100000.0002.001-0'").fetchone()["denda"]
    c.close()
    assert d_a == 0, "tagihan yang sudah tercatat bayar denda-nya dibekukan"
    assert d_b == 2000 and n == 1      # 2 bulan terlampaui x 1%


def test_filter_status_dan_sortir(klien, app):
    masuk(klien)
    impor(klien, KOLOM_KETETAPAN, empat_rows(), "k.xlsx")
    c = conn()
    t1 = c.execute("SELECT id FROM tagihan ORDER BY id LIMIT 1").fetchone()["id"]
    t2 = c.execute("SELECT id FROM tagihan ORDER BY id LIMIT 1 OFFSET 1").fetchone()["id"]
    c.close()

    # filter "Belum bayar" dulu selalu kosong (bug lama) -> kini berisi
    html = klien.get("/data?status=belum").get_data(as_text=True)
    assert "Wajib Pajak 1" in html and "Masih harus ditagih" in html

    # bayar: WP1 via bank (otomatis lunas), WP2 sebagian via petugas (diproses)
    bayar(klien, t1, jumlah="999999", metode="bank")
    bayar(klien, t2, jumlah="1000", metode="petugas")
    assert "Wajib Pajak 1" not in klien.get("/data?status=belum").get_data(as_text=True)
    assert "Wajib Pajak 2" not in klien.get("/data?status=belum").get_data(as_text=True)
    assert "Wajib Pajak 1" in klien.get("/data?status=lunas").get_data(as_text=True)
    assert "Wajib Pajak 2" in klien.get("/data?status=proses").get_data(as_text=True)

    # sortir: terbesar dulu, lalu urut nama (halaman ini tinggal WP3 & WP4)
    r = klien.get("/data?sortir=besar&status=belum").get_data(as_text=True)
    assert r.index("Wajib Pajak 4") < r.index("Wajib Pajak 3")
    r = klien.get("/data?sortir=nama&status=belum").get_data(as_text=True)
    assert r.index("Wajib Pajak 3") < r.index("Wajib Pajak 4")


def test_denda_tampil_di_daftar_dan_total(klien, app):
    masuk(klien)
    y = str(dt.date.today().year - 1)          # tahun lalu: pasti sudah lewat jatuh tempo
    rows = [[i + 1, f"35.18.010000{i}.000{i + 1}.{i + 2:02d}-0", y, f"WP Denda {i + 1}",
             f"Jl. Denda {i + 1}", f"Jl. Denda {i + 1}", 100, 40, 100000, "Blok 1"] for i in range(2)]
    impor(klien, KOLOM_KETETAPAN, rows, "d.xlsx", tahun=y)
    c = conn()
    from core.db import set_settings
    set_settings(c, {"jatuh_tempo_bulan": "1"})
    c.commit()
    c.close()

    html = klien.get(f"/data?tahun={y}").get_data(as_text=True)
    assert "Masih harus ditagih" in html and "Termasuk denda" in html and "Denda" in html
    c = conn()
    d = c.execute("SELECT denda FROM tagihan LIMIT 1").fetchone()["denda"]
    tid = c.execute("SELECT id FROM tagihan LIMIT 1").fetchone()["id"]
    c.close()
    assert d >= 12000, d                                       # min. 12 bulan x 1%
    body = klien.get(f"/bayar/{tid}").get_data(as_text=True)
    assert "Total harus dibayar" in body and "Denda" in body   # kwitansi ikut pokok+denda


def test_usulan_pindah_blok(klien, app):
    masuk(klien)
    impor(klien, KOLOM_KETETAPAN, empat_rows(), "k.xlsx")
    post(klien, "/petugas/simpan", nama="Petugas Dua", blok="Blok 2", aktif="1",
         buat_user="1", password="rahasia1")
    c = conn()
    p1 = c.execute("SELECT id FROM petugas WHERE blok='Blok 1'").fetchone()["id"]
    p2 = c.execute("SELECT id FROM petugas WHERE blok='Blok 2'").fetchone()["id"]
    t1 = c.execute("SELECT id FROM tagihan ORDER BY id LIMIT 1").fetchone()["id"]
    t2 = c.execute("SELECT id FROM tagihan ORDER BY id LIMIT 1 OFFSET 1").fetchone()["id"]
    c.close()

    r = post(klien, "/pindah/ajukan", tagihan_id=t1, petugas_baru=p2, alasan="pindah rumah")
    assert "dikirim" in r.get_data(as_text=True)
    r = post(klien, "/pindah/ajukan", tagihan_id=t1, petugas_baru=p2, alasan="dua kali")
    assert "sudah ada usulan" in r.get_data(as_text=True)
    r = post(klien, "/pindah/ajukan", tagihan_id=t2, petugas_baru=p1, alasan="blok sama")
    assert "sama dengan blok" in r.get_data(as_text=True)

    c = conn()
    uid = c.execute("SELECT id FROM usulan_pindah WHERE tagihan_id=?", (t1,)).fetchone()["id"]
    c.close()
    r = post(klien, f"/pindah/putuskan/{uid}", keputusan="setuju")
    assert "disetujui" in r.get_data(as_text=True)
    c = conn()
    assert c.execute("SELECT petugas_id FROM tagihan WHERE id=?", (t1,)).fetchone()["petugas_id"] == p2
    c.close()
    r = post(klien, f"/pindah/putuskan/{uid}", keputusan="tolak")
    assert "sudah diputuskan" in r.get_data(as_text=True)

    # petugas blok 2 kini memegang NOP itu, bisa mengajukan balik; admin menolak
    masuk(klien, "petugas-dua", "rahasia1")
    assert "Wajib Pajak 1" in klien.get("/data?tahun=2026").get_data(as_text=True)
    r = post(klien, "/pindah/ajukan", tagihan_id=t1, petugas_baru=p1, alasan="balik lagi")
    assert "dikirim" in r.get_data(as_text=True)
    r = post(klien, "/pindah/ajukan", tagihan_id=t2, petugas_baru=p2, alasan="bukan milikku")
    assert "bukan tanggungan blok Anda" in r.get_data(as_text=True)
    masuk(klien)
    c = conn()
    uid2 = c.execute("SELECT id FROM usulan_pindah WHERE tagihan_id=? AND status='menunggu'",
                     (t1,)).fetchone()["id"]
    c.close()
    r = post(klien, f"/pindah/putuskan/{uid2}", keputusan="tolak", catatan="bukan kewenangan")
    assert "ditolak" in r.get_data(as_text=True)
    c = conn()
    assert c.execute("SELECT petugas_id FROM tagihan WHERE id=?", (t1,)).fetchone()["petugas_id"] == p2
    c.close()


def test_laporan_dan_xlsx_memuat_kekurangan(klien, app):
    masuk(klien)
    impor(klien, KOLOM_KETETAPAN, empat_rows(), "k.xlsx")
    html = klien.get("/laporan?tahun=2026").get_data(as_text=True)
    assert "Kekurangan" in html
    assert "90.000" in html or "90,000" in html or "Rp" in html   # 4 x tagihan masih jadi kekurangan
    r = klien.get("/laporan/progres.xlsx")
    assert r.status_code == 200 and len(r.data) > 5000


def test_login_petugas_dari_buat_sampai_nonaktif(klien, app):
    """Siklus akun petugas pemungut: dibuat admin -> login -> ganti pw -> nonaktif."""
    masuk(klien)
    impor(klien, KOLOM_KETETAPAN, empat_rows(), "k.xlsx")          # 4 NOP di Blok 1
    r = post(klien, "/petugas/simpan", nama="Coba Satu", blok="Blok 5", aktif="1",
             buat_user="1", password="rahasia77")
    assert "user 'coba-satu' password 'rahasia77'" in r.get_data(as_text=True)

    # password salah ditolak
    r = klien.post("/login", data={"_csrf": "", "username": "coba-satu", "password": "salah"})
    assert "User atau password salah" in r.get_data(as_text=True)

    # login benar: masuk, hanya blok sendiri, menu admin terkunci
    masuk(klien, "coba-satu", "rahasia77")
    assert klien.get("/").status_code == 302                # petugas -> daftar ketetapan
    for halaman in ("/pengaturan", "/laporan", "/impor", "/petugas"):
        assert klien.get(halaman).status_code == 403, halaman
    html = klien.get("/data?tahun=2026").get_data(as_text=True)
    assert "Coba Satu" in html and "Wajib Pajak 1" not in html

    # reset password oleh admin -> password lama mati, yang baru hidup
    masuk(klien)
    c = conn()
    uid = c.execute("SELECT id FROM users WHERE username='coba-satu'").fetchone()["id"]
    c.close()
    post(klien, "/petugas/user/reset", id=str(uid), password="pwbaru99")
    masuk(klien, "coba-satu", "pwbaru99")

    # nonaktifkan -> tidak bisa login lagi
    masuk(klien)
    c = conn()
    conn_exec = c.execute("UPDATE users SET aktif=0 WHERE id=?", (uid,))
    c.commit()
    c.close()
    r = klien.post("/login", data={"_csrf": "", "username": "coba-satu", "password": "pwbaru99"})
    assert "User atau password salah" in r.get_data(as_text=True)


def test_hapus_petugas(klien, app):
    masuk(klien)
    impor(klien, KOLOM_KETETAPAN, empat_rows(), "k.xlsx")          # 4 NOP di Blok 1
    post(klien, "/petugas/simpan", nama="Petugas Dua", blok="Blok 2", aktif="1",
         buat_user="1", password="rahasia1")
    c = conn()
    p1 = c.execute("SELECT id FROM petugas WHERE blok='Blok 1'").fetchone()["id"]
    p2 = c.execute("SELECT id FROM petugas WHERE blok='Blok 2'").fetchone()["id"]
    t1 = c.execute("SELECT id FROM tagihan ORDER BY id LIMIT 1").fetchone()["id"]
    c.close()
    # usulan pindah ke Blok 2 sedang menunggu (uji auto-tolak saat Blok 2 dihapus)
    post(klien, "/pindah/ajukan", tagihan_id=t1, petugas_baru=p2, alasan="uji")

    # hapus tanpa pilihan pengganti padahal masih pegang NOP -> ditolak
    r = post(klien, "/petugas/hapus", id=str(p1))
    assert "masih memegang 4 NOP" in r.get_data(as_text=True)

    # hapus Blok 1, NOP diserahkan ke Blok 2
    r = post(klien, "/petugas/hapus", id=str(p1), pindah_ke=str(p2))
    b = r.get_data(as_text=True)
    assert "dihapus" in b and "4 NOP dipindah" in b and "Blok 2" in b
    c = conn()
    assert c.execute("SELECT COUNT(*) c FROM tagihan WHERE petugas_id=?", (p1,)).fetchone()["c"] == 0
    assert c.execute("SELECT COUNT(*) c FROM tagihan WHERE petugas_id=?", (p2,)).fetchone()["c"] == 4
    assert c.execute("SELECT COUNT(*) c FROM petugas WHERE id=?", (p1,)).fetchone()["c"] == 0
    c.close()

    # petugas Blok 2 masih bisa login (akunnya hidup)
    masuk(klien, "petugas-dua", "rahasia1")
    masuk(klien)

    # hapus Blok 2 dengan 'lepas' -> NOP tanpa petugas, usulan ditolak otomatis, akun nonaktif
    r = post(klien, "/petugas/hapus", id=str(p2), pindah_ke="lepas")
    assert "dihapus" in r.get_data(as_text=True) and "dilepas" in r.get_data(as_text=True)
    c = conn()
    assert c.execute("SELECT COUNT(*) c FROM petugas WHERE id=?", (p2,)).fetchone()["c"] == 0
    assert c.execute("SELECT COUNT(*) c FROM tagihan WHERE petugas_id IS NULL").fetchone()["c"] == 4
    assert c.execute("SELECT status FROM usulan_pindah WHERE tagihan_id=?",
                     (t1,)).fetchone()["status"] == "ditolak"
    assert c.execute("SELECT aktif FROM users WHERE username='petugas-dua'").fetchone()["aktif"] == 0
    c.close()
    r = klien.post("/login", data={"_csrf": "", "username": "petugas-dua", "password": "rahasia1"})
    assert "User atau password salah" in r.get_data(as_text=True)


def test_buat_akun_login_lewat_ubah_petugas(klien, app):
    """Petugas placeholder hasil impor (tanpa akun) bisa diberi akun lewat form UBAH."""
    masuk(klien)
    impor(klien, KOLOM_KETETAPAN,
          [[1, "35.18.0100000.0001.001-0", "2026", "WP 1", "Jl. 1", "Jl. 1", 100, 40, 30000, "darsono"]],
          "k.xlsx")
    c = conn()
    p = c.execute("SELECT * FROM petugas").fetchone()
    assert p["nama"] == "Petugas darsono" and p["blok"] == "darsono"   # placeholder otomatis
    pid = p["id"]
    c.close()

    # form ubah tersedia dan menawarkan buat akun (belum punya)
    html = klien.get(f"/petugas?edit={pid}").get_data(as_text=True)
    assert "belum punya" in html and 'name="buat_user"' in html

    # ubah jadi data benar + buat akun
    r = post(klien, "/petugas/simpan", id=str(pid), nama="Darsono", blok="Blok 1",
             aktif="1", buat_user="1", password="rahasia5")
    assert "user 'darsono' password 'rahasia5'" in r.get_data(as_text=True)

    # petugas bisa login dan melihat NOP-nya
    masuk(klien, "darsono", "rahasia5")
    assert "WP 1" in klien.get("/data?tahun=2026").get_data(as_text=True)

    # ubah lagi: centang buat_user tidak menduplikat akun
    masuk(klien)
    r = post(klien, "/petugas/simpan", id=str(pid), nama="Darsono", blok="Blok 1",
             aktif="1", buat_user="1", password="x")
    assert "sudah ada — akun tidak dibuat ulang" in r.get_data(as_text=True)
    c = conn()
    assert c.execute("SELECT COUNT(*) c FROM users WHERE username='darsono'").fetchone()["c"] == 1
    c.close()


def test_menu_petugas_dan_papan_progres_rp(klien, app):
    """Menu petugas tinggal 3, landing di /data, kartu HP tersedia, papan progres ada nilai Rp."""
    masuk(klien)
    impor(klien, KOLOM_KETETAPAN, empat_rows(), "k.xlsx")          # Blok 1: 4 NOP

    # papan progres: nilai ketetapan & belum terbayar per petugas + total
    html = klien.get("/monitor?tahun=2026").get_data(as_text=True)
    assert "Nilai ketetapan" in html and "Belum terbayar" in html
    c = conn()
    tot_target = c.execute("SELECT COALESCE(SUM(pokok+denda),0) v FROM tagihan WHERE tahun_pajak='2026'").fetchone()["v"]
    c.close()
    import re as _re
    m = _re.search(r"Belum terbayar</div><div class=\"val\"[^>]*>([^<]+)", html)
    assert m and _re.sub(r"[^0-9]", "", m.group(1)) == str(tot_target)  # belum ada bayar -> kekurangan == target

    post(klien, "/petugas/simpan", nama="Darsono", blok="Blok 1", aktif="1",
         buat_user="1", password="rahasia5")
    masuk(klien, "darsono", "rahasia5")

    # landing petugas = daftar ketetapan; mode senior aktif; kartu HP tersedia
    r = klien.get("/", follow_redirects=True)
    html = r.get_data(as_text=True)
    assert "mode-petugas" in html and 'class="kartu' in html
    # menu: hanya ketetapan, pindah blok, papan progres
    for menu in ("Ketetapan", "Pindah Blok", "Papan Progres"):
        assert menu in html, menu
    for menu in ("Transaksi", "Antrean Billing", "Cetak Ulang", "Beranda", "Laporan"):
        assert menu not in html, menu
    # papan progres tetap bisa dibuka petugas
    assert "Belum terbayar" in klien.get("/monitor?tahun=2026").get_data(as_text=True)
