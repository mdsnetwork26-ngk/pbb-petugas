"""Mode Kasir — satu pembayaran untuk banyak NOP (satu kwitansi gabungan).

Alur: petugas/admin memasukkan beberapa NOP (nyaman sampai ±20) ke keranjang,
lalu satu tombol Bayar. Setelah itu muncul dialog "uang diterima" sebagai alat
bantu hitung kembalian, dan pilihan:
  • Rekam & Cetak  -> kwitansi gabungan (satu nomor, semua NOP dirinci) dibuka untuk dicetak;
  • Rekam Saja     -> pembayaran tetap terekam + PDF tetap dibuat & masuk riwayat,
                      tapi tidak dibuka (wajib pajak membawa SPPT sendiri untuk dicap lunas).

Rancangan data (tanpa migrasi/ALTER tabel lama):
  • Tiap NOP tetap dicatat lewat services.catat_pembayaran (metode 'petugas')
    sehingga semua aturan lama (status, denda beku, antrean billing eSPPT) berjalan apa adanya.
  • Baris pertama memakai nomor kwitansi N (MM/YYYY/NNN). Baris berikutnya diberi
    nomor N.2, N.3, dst. — kolom nomor_kwitansi tetap UNIQUE, nomor tidak pernah
    didaur ulang, dan rekap/cetak ulang lama tetap berfungsi tanpa perubahan.
  • Info tingkat grup (uang diterima, kembalian, mode cetak) disimpan di tabel baru
    kasir_transaksi yang dibuat idempoten (CREATE TABLE IF NOT EXISTS) saat aplikasi start.
"""
from __future__ import annotations

import datetime as dt
import io
import re

from flask import Blueprint, abort, g, jsonify, render_template, request, send_file, url_for
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

from . import format as F
from . import pdf as P
from . import services as S
from .db import DATA_DIR, connect, get_settings, now

bp = Blueprint("kasir", __name__)

MAKS_ITEM = 30          # batas aman server; pemakaian nyaman ±20 NOP per pembayar
MAKS_HASIL_CARI = 50

SCHEMA_KASIR = """
CREATE TABLE IF NOT EXISTS kasir_transaksi(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  nomor_kwitansi TEXT NOT NULL UNIQUE,      -- nomor baris pertama (dasar, tanpa sufiks .2/.3)
  jumlah_nop INTEGER NOT NULL DEFAULT 0,
  total INTEGER NOT NULL DEFAULT 0,
  uang_diterima INTEGER,                    -- boleh kosong (hanya alat bantu hitung kembalian)
  kembalian INTEGER,
  mode_cetak TEXT NOT NULL DEFAULT 'cetak', -- cetak | rekam_saja
  pembayar TEXT DEFAULT '',
  petugas_nama TEXT DEFAULT '',
  user_id INTEGER,
  created_at TEXT
);
"""


def init_db_kasir() -> None:
    """Buat tabel kasir bila belum ada. Idempoten — aman untuk database lama."""
    conn = connect()
    try:
        conn.executescript(SCHEMA_KASIR)
        conn.commit()
    finally:
        conn.close()


# ------------------------------------------------------------------ pencarian

def cari_nop(conn, q: str, tahun: str, petugas_id: int | None = None,
             limit: int = MAKS_HASIL_CARI) -> list[dict]:
    """Cari NOP aktif pada tahun berjalan. Baris yang sudah lunas/proses tetap
    dikembalikan dengan tanda statusnya supaya kasir bisa menjelaskan ke WP."""
    q = (q or "").strip()
    if not q:
        return []
    where = ["t.aktif = 1", "t.tahun_pajak = ?"]
    args: list = [tahun]
    if petugas_id:
        where.append("t.petugas_id = ?")
        args.append(petugas_id)
    digits = re.sub(r"\D", "", q)
    if len(digits) >= 8 and not re.search(r"[A-Za-z]", q):
        # mencari dengan angka NOP: titik/strip di kolom diabaikan agar cocok dengan
        # NOP apa pun yang diketik (polos, bertitik, atau terpotong 0 depannya)
        where.append("REPLACE(REPLACE(REPLACE(t.nop,'.',''),'-',''),' ','') LIKE ?")
        args.append(f"%{digits}%")
    else:
        like = f"%{q}%"
        where.append("(t.nop LIKE ? OR t.nama_wp LIKE ? OR t.alamat_objek LIKE ? OR t.alamat_wp LIKE ?)")
        args += [like, like, like, like]
    sql = S.tagihan_view_sql("WHERE " + " AND ".join(where), "t.nop")
    out = []
    for r in conn.execute(sql, args):
        if len(out) >= limit:
            break
        d = dict(r)
        d["status"] = S.status_tagihan(d)
        d["total"] = int(d["pokok"] or 0) + int(d["denda"] or 0)
        out.append(d)
    return out


# ------------------------------------------------------------------- pembayaran

def bayar_grup(conn, items: list, pembayar: str, uang, mode: str, user: dict,
               petugas_scope: int | None = None) -> dict:
    """Catat pembayaran banyak NOP sekaligus. SEMUA terjadi dalam satu transaksi:
    bila satu NOP bermasalah (sudah lunas, di luar blok, dsb.) seluruhnya dibatalkan
    (rollback dilakukan pemanggil)."""
    if not items:
        raise ValueError("Keranjang masih kosong — pilih minimal satu NOP.")
    if len(items) > MAKS_ITEM:
        raise ValueError(f"Terlalu banyak NOP dalam satu pembayaran (maksimal {MAKS_ITEM}).")
    if mode not in ("cetak", "rekam_saja"):
        raise ValueError("Mode cetak tidak dikenal (pakai 'cetak' atau 'rekam_saja').")

    ids: list[int] = []
    for it in items:
        try:
            tid = int((it or {}).get("id"))
        except (TypeError, ValueError):
            raise ValueError("Item keranjang tidak valid.")
        if tid in ids:
            raise ValueError("Ada NOP yang masuk dua kali ke keranjang.")
        ids.append(tid)

    S.perbarui_denda(conn)          # denda terbaru sebelum nominal dikunci

    sql = S.tagihan_view_sql(f"WHERE t.id IN ({','.join('?' * len(ids))})", "t.nop")
    rows = {int(r["id"]): dict(r) for r in conn.execute(sql, ids)}
    rincian: list[dict] = []
    total = 0
    for tid in ids:                 # ikuti urutan keranjang
        r = rows.get(tid)
        if not r:
            raise ValueError("Data ketetapan tidak ditemukan (mungkin sudah dihapus/nonaktif).")
        if petugas_scope and int(r["petugas_id"] or 0) != int(petugas_scope):
            raise ValueError(f"NOP {r['nop']} bukan milik blok Anda.")
        if S.status_tagihan(r) != "belum_bayar":
            raise ValueError(
                f"NOP {r['nop']} sudah tercatat lunas/diproses. "
                "Keluarkan dari keranjang lalu ulangi (kemungkinan baru dibayar orang lain).")
        r["total"] = int(r["pokok"] or 0) + int(r["denda"] or 0)
        total += r["total"]
        rincian.append(r)

    uang_i = None
    if uang not in (None, ""):
        try:
            uang_i = int(uang)
        except (TypeError, ValueError):
            raise ValueError("Uang diterima harus berupa angka.")
        if uang_i < total:
            raise ValueError("Uang diterima kurang dari total tagihan.")
    kembalian = (uang_i - total) if uang_i is not None else None

    pembayar = (pembayar or "").strip()
    nomor = None
    pembayaran_ids: list[int] = []
    for i, r in enumerate(rincian, start=1):
        res = S.catat_pembayaran(conn, int(r["id"]), {
            "jumlah": r["total"],
            "metode": "petugas",
            "pembayar": pembayar or r["nama_wp"],
            "catatan": f"Kasir: pembayaran gabungan {len(rincian)} NOP"
                       + (f" dari {pembayar}" if pembayar else ""),
        }, r["petugas_id"], user.get("role", "admin"))
        if i == 1:
            nomor = res["nomor_kwitansi"]
        else:
            # sufiks .2/.3/... -> satu nomor kwitansi untuk satu grup (kolom tetap UNIQUE)
            conn.execute("UPDATE pembayaran SET nomor_kwitansi=? WHERE id=?",
                         (f"{nomor}.{i}", res["id"]))
        pembayaran_ids.append(res["id"])
    if not nomor:
        raise RuntimeError("Gagal membuat nomor kwitansi grup.")

    conn.execute(
        """INSERT INTO kasir_transaksi(nomor_kwitansi,jumlah_nop,total,uang_diterima,kembalian,
             mode_cetak,pembayar,petugas_nama,user_id,created_at)
           VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (nomor, len(rincian), total, uang_i, kembalian, mode,
         pembayar or rincian[0]["nama_wp"], (user.get("nama") or ""), user.get("id"), now()))

    return {"nomor": nomor, "total": total, "uang_diterima": uang_i, "kembalian": kembalian,
            "jumlah_nop": len(rincian), "mode_cetak": mode, "pembayaran_ids": pembayaran_ids,
            "rincian": rincian}


# ------------------------------------------------------------------ data grup

def anggota_grup(conn, nomor: str) -> list[dict]:
    """Semua baris pembayaran satu kwitansi gabungan (dasar + sufiks .2/.3/...)."""
    sql = """SELECT b.id AS pembayaran_id, b.nomor_kwitansi, b.tgl_bayar, b.status,
                    b.jumlah_diterima, b.billing_code, b.metode, b.pembayar, b.void,
                    t.id AS tagihan_id, t.nop, t.nama_wp, t.alamat_objek, t.tahun_pajak,
                    t.pokok, t.denda,
                    p.nama AS petugas_nama, p.nip AS petugas_nip, p.blok AS petugas_blok,
                    p.jabatan AS petugas_jabatan
             FROM pembayaran b JOIN tagihan t ON t.id = b.tagihan_id
             LEFT JOIN petugas p ON p.id = b.petugas_id
             WHERE b.void = 0 AND (b.nomor_kwitansi = ? OR b.nomor_kwitansi LIKE ?)
             ORDER BY LENGTH(b.nomor_kwitansi), b.nomor_kwitansi"""
    return [dict(r) for r in conn.execute(sql, (nomor, nomor + ".%"))]


def grup_transaksi(conn, nomor: str) -> dict | None:
    r = conn.execute("SELECT * FROM kasir_transaksi WHERE nomor_kwitansi=?", (nomor,)).fetchone()
    return dict(r) if r else None


def daftar_riwayat(conn, limit: int = 25, user_id: int | None = None) -> list[dict]:
    if user_id:
        rows = conn.execute("SELECT * FROM kasir_transaksi WHERE user_id=? "
                            "ORDER BY id DESC LIMIT ?", (user_id, limit))
    else:
        rows = conn.execute("SELECT * FROM kasir_transaksi ORDER BY id DESC LIMIT ?", (limit,))
    return [dict(r) for r in rows]


def simpan_pdf_grup(rows: list[dict], tr: dict | None, settings: dict, nomor: str):
    """Buat (atau tulis ulang) file PDF kwitansi gabungan; gagal -> None (pembayaran tetap sah)."""
    try:
        safe = str(nomor).replace("/", "-")
        path = DATA_DIR / "cetak" / f"kwitansi-gab-{safe}.pdf"
        petugas = None
        for r in rows:
            if r.get("petugas_nama"):
                petugas = {"nama": r["petugas_nama"], "nip": r.get("petugas_nip") or "",
                           "jabatan": r.get("petugas_jabatan") or "", "blok": r.get("petugas_blok") or ""}
                break
        return buat_pdf_grup(rows, tr, settings, path, petugas)
    except Exception:
        return None


# ------------------------------------------------------------- PDF gabungan

_GARIS = colors.HexColor("#1F4E79")
_ABU = colors.HexColor("#666666")
_BOX = colors.HexColor("#C7D2E0")
_ROW = 6.2 * mm
_CAP_AWAL, _CAP_LANJUTAN = 18, 30


def _kop_grup(c, d: dict, W: float, H: float, ml: float, rx: float,
              nomor_halaman: int, total_halaman: int, pembayar: str, tahun: str, n_nop: int) -> float:
    """Gambar bingkai + kop + judul + nomor/tanggal. Kembalikan y awal tabel."""
    inner_w = rx - ml
    c.setLineWidth(1.4)
    c.setStrokeColor(_GARIS)
    c.rect(10 * mm, 10 * mm, W - 20 * mm, H - 20 * mm)
    y = H - 22 * mm
    c.setFillColor(_GARIS)
    c.setFont("Helvetica-Bold", 13)
    c.drawCentredString(W / 2, y, d["kop1"])
    y -= 5.6 * mm
    c.setFont("Helvetica-Bold", 12)
    c.drawCentredString(W / 2, y, d["kop2"])
    y -= 5.2 * mm
    c.setFont("Helvetica-Bold", 14)
    c.drawCentredString(W / 2, y, d["kop3"])
    y -= 5 * mm
    c.setLineWidth(2)
    c.line(ml, y, rx, y)
    c.setLineWidth(0.6)
    c.line(ml, y - 1.4 * mm, rx, y - 1.4 * mm)

    y -= 12 * mm
    c.setFillColor(colors.black)
    c.setFont("Helvetica-Bold", 10.5)
    c.drawCentredString(W / 2, y, "KWITANSI GABUNGAN")
    c.setFont("Helvetica", 8)
    c.drawCentredString(W / 2, y - 4.6 * mm,
                        "PEMBAYARAN PAJAK BUMI DAN BANGUNAN PERKOTAAN/PERDESAAN (PBB-P2)")
    y -= 13 * mm
    c.setFont("Helvetica", 9.5)
    c.drawString(ml, y, f"Nomor    : {d['nomor']}")
    c.drawRightString(rx, y, f"Tanggal  : {d['tgl_cetak']}")
    y -= 5.5 * mm
    c.drawString(ml, y, f"Telah terima dari : {pembayar}")
    y -= 5.5 * mm
    c.drawString(ml, y, f"Untuk pembayaran  : PBB-P2 tahun pajak {tahun} — {n_nop} NOP (rincian di bawah)")
    if total_halaman > 1:
        c.setFont("Helvetica-Oblique", 8)
        c.setFillColor(_ABU)
        c.drawRightString(rx, y, f"Halaman {nomor_halaman} dari {total_halaman}")
        c.setFillColor(colors.black)
    return y - 6 * mm, inner_w


def buat_pdf_grup(rows: list[dict], tr: dict | None, settings: dict, path,
                  petugas: dict | None = None):
    """Kwitansi gabungan satu berkas A4. Bila baris tidak muat, lanjut ke halaman
    berikutnya: kop + nomor diulang, TOTAL & terbilang hanya di halaman terakhir."""
    if not rows:
        raise ValueError("Tidak ada baris pembayaran untuk kwitansi gabungan ini.")
    tr = tr or {}
    nomor = rows[0]["nomor_kwitansi"]
    tgl0 = F.to_date(rows[0].get("tgl_bayar")) or dt.date.today()
    tahun = str(rows[0].get("tahun_pajak") or tgl0.year)
    total = sum(int(r.get("jumlah_diterima") or 0) for r in rows)
    pembayar = (tr.get("pembayar") or rows[0].get("pembayar") or rows[0].get("nama_wp") or "-")
    lunas = all((r.get("status") == "terverifikasi") for r in rows)

    # kop/label/penandatangan memakai format_kwitansi agar tidak pernah beda gaya
    d = P.format_kwitansi({
        "nomor_kwitansi": nomor, "nop": "", "nama_wp": pembayar, "pembayar": pembayar,
        "tgl_bayar": rows[0].get("tgl_bayar"), "tahun_pajak": tahun, "pokok": 0, "denda": 0,
        "jumlah_diterima": total, "metode": "petugas",
        "status": "terverifikasi" if lunas else "menunggu_billing",
        "billing_code": "", "alamat_objek": "",
    }, settings, petugas)
    d["status"] = ("LUNAS (terverifikasi realisasi eSPPT)" if lunas
                   else "Uang diterima — billing eSPPT sedang diproses")
    cek = P.cek_kode({"nomor_kwitansi": nomor, "nop": f"{len(rows)} NOP",
                      "jumlah_diterima": total})

    path = str(path)
    import os
    os.makedirs(os.path.dirname(path), exist_ok=True)
    c = canvas.Canvas(path, pagesize=A4)
    W, H = A4
    ml = mr = 20 * mm
    rx = W - mr

    # pecah baris ke beberapa halaman
    pages: list[list[dict]] = []
    sisa, cap = list(rows), _CAP_AWAL
    while sisa:
        pages.append(sisa[:cap])
        sisa = sisa[cap:]
        cap = _CAP_LANJUTAN

    x_no, x_nop = ml + 2 * mm, ml + 11 * mm
    x_nama = ml + 62 * mm
    x_pokok, x_denda, x_jumlah = rx - 54 * mm, rx - 28 * mm, rx - 3 * mm
    nomor_urut = 0
    for pi, chunk in enumerate(pages):
        terakhir = (pi == len(pages) - 1)
        y, inner_w = _kop_grup(c, d, W, H, ml, rx, pi + 1, len(pages), pembayar, tahun, len(rows))
        y = _tabel_header(c, y, x_no, x_nop, x_nama, x_pokok, x_denda, x_jumlah, ml, rx)
        for r in chunk:
            nomor_urut += 1
            y -= _ROW
            c.setFont("Helvetica", 8.4)
            c.setFillColor(colors.black)
            c.drawString(x_no, y, str(nomor_urut))
            c.setFont("Courier", 8.2)
            c.drawString(x_nop, y, str(r["nop"]))
            c.setFont("Helvetica", 8.4)
            nama = str(r.get("nama_wp") or "-")
            c.drawString(x_nama, y, (nama[:30] + "..") if len(nama) > 32 else nama)
            c.drawRightString(x_pokok, y, F.angka(r.get("pokok") or 0))
            c.drawRightString(x_denda, y, F.angka(r.get("denda") or 0))
            c.setFont("Helvetica-Bold", 8.4)
            c.drawRightString(x_jumlah, y, F.angka(r.get("jumlah_diterima") or 0))
            c.setStrokeColor(colors.HexColor("#EEF1F5"))
            c.setLineWidth(0.4)
            c.line(ml, y - 1.8 * mm, rx, y - 1.8 * mm)
        if terakhir:
            y = _total_dan_tangan(c, d, y, x_jumlah, ml, rx, len(rows), total, cek, settings)
        else:
            y -= _ROW + 2 * mm
            c.setFont("Helvetica-Oblique", 8.4)
            c.setFillColor(_ABU)
            c.drawCentredString((ml + rx) / 2, y, "… rincian berlanjut ke halaman berikutnya — "
                                                   "TOTAL ada di halaman terakhir …")
        c.showPage()
    c.save()
    return path


def _tabel_header(c, y, x_no, x_nop, x_nama, x_pokok, x_denda, x_jumlah, ml, rx) -> float:
    y -= 5.6 * mm
    c.setFillColor(_GARIS)
    c.rect(ml, y - 1.6 * mm, rx - ml, 5.6 * mm, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 7.8)
    c.drawString(x_no, y, "NO")
    c.drawString(x_nop, y, "NOP")
    c.drawString(x_nama, y, "NAMA WAJIB PAJAK")
    c.drawRightString(x_pokok, y, "POKOK (Rp)")
    c.drawRightString(x_denda, y, "DENDA (Rp)")
    c.drawRightString(x_jumlah, y, "JUMLAH (Rp)")
    c.setFillColor(colors.black)
    return y - 1.6 * mm


def _total_dan_tangan(c, d: dict, y, x_jumlah, ml, rx, n_nop, total, cek, settings) -> float:
    y -= 7.5 * mm
    c.setStrokeColor(_GARIS)
    c.setLineWidth(1)
    c.line(ml, y + 3 * mm, rx, y + 3 * mm)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(ml + 2 * mm, y, f"TOTAL  ({n_nop} NOP)")
    c.drawRightString(x_jumlah, y, F.rp(total))
    y -= 5.5 * mm
    c.setFont("Helvetica-Oblique", 9)
    c.drawString(ml + 2 * mm, y, f"Terbilang: {d['terbilang']}")
    if d.get("no_rek"):
        y -= 5 * mm
        c.setFont("Helvetica", 8.2)
        c.drawString(ml + 2 * mm, y, "Setor ke : " + d["no_rek"])

    # ---- tanda tangan ----
    y -= 12 * mm
    lebar = float(rx - ml)
    cx_kiri = ml + lebar * 0.22
    cx_kanan = ml + lebar * 0.78
    c.setFont("Helvetica", 9)
    c.drawCentredString(cx_kiri, y, "Menyetor,")
    c.drawCentredString(cx_kiri, y - 5 * mm, "Wajib Pajak / Kuasa")
    c.drawCentredString(cx_kanan, y,
                        "Petugas Pemungut PBB-P2 " + (d["blok"] or "") + ",")
    c.setFont("Helvetica-Oblique", 8.4)
    c.setFillColor(_ABU)
    c.drawCentredString(cx_kiri, y - 20 * mm, "( ......................................... )")
    c.setFillColor(colors.black)
    c.setFont("Helvetica-Bold", 9.8)
    c.drawCentredString(cx_kanan, y - 20 * mm, d["petugas_nama"])
    if d["petugas_nip"]:
        c.setFont("Helvetica", 8)
        c.drawCentredString(cx_kanan, y - 24.5 * mm, "NIP. " + d["petugas_nip"])

    # ---- mengetahui (Lurah/Kepala Desa) ----
    yb = y - 30 * mm
    if d["lurah_nama"]:
        c.setFont("Helvetica", 8.6)
        c.drawCentredString(cx_kanan, yb,
                            "Mengetahui, " + (settings.get("jabatan_lurah") or "Lurah / Kepala Desa"))
        c.setStrokeColor(colors.HexColor("#9aa4b2"))
        c.setLineWidth(0.5)
        c.line(cx_kanan - 25 * mm, yb - 4 * mm, cx_kanan + 25 * mm, yb - 4 * mm)
        c.setFont("Helvetica-Bold", 9.4)
        c.drawCentredString(cx_kanan, yb - 22 * mm, d["lurah_nama"])
        if d["lurah_nip"]:
            c.setFont("Helvetica", 8)
            c.drawCentredString(cx_kanan, yb - 26.5 * mm, "NIP. " + d["lurah_nip"])
        yb -= 30 * mm

    # ---- kaki ----
    kaki = [f"Status setoran : {d['status']}",
            f"Kode verifikasi : {cek}  |  Cek keaslian di menu 'Cek Kwitansi' "
            f"(masukkan nomor dasar {d['nomor']})."]
    if d.get("catatan"):
        kaki.append("Catatan : " + d["catatan"])
    tinggi = 6.2 * mm * len(kaki) + 3 * mm
    fy = max(yb - 8 * mm, 14 * mm + tinggi)
    c.setStrokeColor(_BOX)
    c.setLineWidth(0.6)
    c.rect(ml, fy - tinggi, rx - ml, tinggi)
    c.setFont("Helvetica", 7.6)
    for i, teks in enumerate(kaki, start=1):
        c.drawString(ml + 4 * mm, fy - i * 6.2 * mm, teks[:160])
    c.setFont("Helvetica-Oblique", 7)
    c.drawRightString(rx - 2 * mm, 12 * mm,
                      f"Dicetak {dt.datetime.now():%d/%m/%Y %H:%M} — Aplikasi Loket PBB (Mode Kasir)")
    return fy


# --------------------------------------------------------------------- routes

def _scope_petugas():
    u = g.get("user") or {}
    if u.get("role") == "petugas":
        return u.get("petugas_id")
    return None


def _json_gagal(pesan: str, kode: int = 400):
    return jsonify({"ok": False, "pesan": pesan}), kode


@bp.route("/kasir")
def kasir_page():
    tahun = str(dt.date.today().year)
    conn = connect()
    try:
        scope = _scope_petugas()
        riwayat = daftar_riwayat(conn, 25,
                                 user_id=(g.user["id"] if scope else None))
    finally:
        conn.close()
    return render_template("kasir.html", aktif="kasir", tahun=tahun,
                           riwayat=riwayat, maks_item=MAKS_ITEM, scope_petugas=bool(scope))


@bp.route("/kasir/api/cari")
def api_cari():
    q = (request.args.get("q") or "").strip()
    tahun = (request.args.get("tahun") or str(dt.date.today().year))
    conn = connect()
    try:
        S.perbarui_denda(conn)
        rows = cari_nop(conn, q, tahun, _scope_petugas())
    finally:
        conn.close()
    hasil = [{"id": r["id"], "nop": r["nop"], "nama_wp": r["nama_wp"] or "-",
              "alamat_objek": r["alamat_objek"] or "", "pokok": int(r["pokok"] or 0),
              "denda": int(r["denda"] or 0), "total": r["total"], "status": r["status"],
              "blok": r.get("petugas_blok") or ""} for r in rows]
    return jsonify({"ok": True, "tahun": tahun, "hasil": hasil})


@bp.route("/kasir/api/bayar", methods=["POST"])
def api_bayar():
    data = request.get_json(silent=True) or request.form.to_dict() or {}
    items = data.get("items") or []
    if isinstance(items, (str, int)):
        items = [{"id": items}]
    mode = data.get("mode_cetak") or "cetak"
    user = dict(g.get("user") or {})
    conn = connect()
    try:
        try:
            hasil = bayar_grup(conn, items, data.get("pembayar") or "",
                               data.get("uang_diterima"), mode, user, _scope_petugas())
            conn.commit()
        except ValueError as e:
            conn.rollback()
            return _json_gagal(str(e))
        # PDF selalu dibuat (juga untuk "rekam saja") — arsip di data/cetak/;
        # bila gagal, pembayaran tetap sah dan PDF dibuat ulang saat dibuka.
        try:
            rows = anggota_grup(conn, hasil["nomor"])
            tr = grup_transaksi(conn, hasil["nomor"])
            simpan_pdf_grup(rows, tr, get_settings(conn), hasil["nomor"])
        except Exception:
            pass
    finally:
        conn.close()
    return jsonify({
        "ok": True, "nomor": hasil["nomor"], "jumlah_nop": hasil["jumlah_nop"],
        "total": hasil["total"], "uang_diterima": hasil["uang_diterima"],
        "kembalian": hasil["kembalian"], "mode_cetak": hasil["mode_cetak"],
        "terbilang": F.terbilang(hasil["total"]),
        "pdf_url": url_for("kasir.kwitansi_grup_pdf", nomor=hasil["nomor"]),
    })


@bp.route("/kasir/kwitansi/<path:nomor>.pdf")
def kwitansi_grup_pdf(nomor: str):
    conn = connect()
    try:
        rows = anggota_grup(conn, nomor)
        if not rows:
            # mungkin yang dibuka nomor anggota (N.2) — cari nomor dasarnya
            dasar = str(nomor).split(".")[0] if "." in nomor.rsplit("/", 1)[-1] else nomor
            rows = anggota_grup(conn, dasar)
            nomor = dasar
        if not rows:
            abort(404, description="Kwitansi gabungan tidak ditemukan.")
        tr = grup_transaksi(conn, nomor)
        settings = get_settings(conn)
    finally:
        conn.close()
    path = simpan_pdf_grup(rows, tr, settings, nomor)
    if not path:
        abort(500, description="Gagal membuat PDF kwitansi gabungan.")
    # kirim dari memori supaya berkas arsip tidak tertahan terbuka di proses
    from pathlib import Path as _Path

    buf = io.BytesIO(_Path(path).read_bytes())
    buf.seek(0)
    return send_file(buf, mimetype="application/pdf",
                     as_attachment=bool(request.args.get("unduh")),
                     download_name=f"kwitansi-gabungan-{str(nomor).replace('/', '-')}.pdf")
