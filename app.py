"""Aplikasi Loket Pembayaran PBB-P2 — petugas pemungut 7 blok.

Jalankan:
    python app.py                (http://0.0.0.0:8088 — bisa dibuka dari HP petugas via WiFi)
    python app.py --seed-demo    (isi contoh data untuk mencoba aplikasi tanpa file asli)
    python app.py --sync-once    (jalankan sinkron eSPPT sekali dari terminal)
"""
from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import os
import re
import secrets
import sys
import threading
import time
from urllib.parse import urlencode
from pathlib import Path

from flask import (Blueprint, abort, current_app, flash, g, jsonify, redirect, render_template,
                   request, send_file, url_for)

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

from core import format as F          # noqa: E402
from core import services as S        # noqa: E402
from core.auth import admin_perlu, bp as auth_bp, init_app as init_auth  # noqa: E402
from core.db import (BACKUP_DIR, DATA_AUTO_DIR, DATA_DIR, DB_PATH, UPLOAD_DIR,  # noqa: E402
                     connect, daily_backup, db_size_bytes, get_settings, init_db, set_settings)
from core.excel import read_sheet, save, write_table          # noqa: E402
from core.esppt import EspptClient                            # noqa: E402
from core.pdf import buat_pdf, format_kwitansi                # noqa: E402

bp = Blueprint("app", __name__)
IZIN = {".xlsx", ".xlsm", ".xls", ".csv"}
_cache_settings: dict = {"v": None}


def refresh_settings_cache() -> dict:
    _cache_settings["v"] = get_settings()
    return _cache_settings["v"]


# --------------------------------------------------------------------- util

def s_conn():
    return get_settings()


def arg(name, default=""):
    v = request.values.get(name)
    return default if v is None else v.strip()


def tahun_aktual() -> str:
    return arg("tahun") or str(dt.date.today().year)


def paginate(rows: list, per_page: int = 150):
    page = max(1, int(arg("hal", "1") or 1))
    total = len(rows)
    pages = max(1, (total + per_page - 1) // per_page)
    page = min(page, pages)
    return rows[(page - 1) * per_page: page * per_page], page, pages, total


def simpan_upload(file_storage) -> Path:
    if not file_storage or not file_storage.filename:
        raise ValueError("Pilih dulu file Excel-nya.")
    nama = re.sub(r"[^A-Za-z0-9._-]", "_", Path(file_storage.filename).name)[-70:]
    if Path(nama).suffix.lower() not in IZIN:
        raise ValueError("Format file harus .xlsx / .xlsm / .csv. "
                         "Bila file Anda .xls lama, buka di Excel lalu Simpan Sebagai (.xlsx).")
    folder = UPLOAD_DIR / dt.date.today().strftime("%Y-%m-%d")
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / f"{dt.datetime.now():%H%M%S}-{nama}"
    file_storage.save(dest)
    return dest


STATUS_LABEL = {
    "belum_tagihan": ("Uang diterima, belum di-billing", "warn"),
    "menunggu_billing": ("Menunggu billing eSPPT", "warn"),
    "terkirim": ("Billing dibuat, menunggu lunas", "info"),
    "terverifikasi": ("LUNAS (cocok dengan realisasi)", "ok"),
}


# ------------------------------------------------------------------ halaman

@bp.route("/")
def dashboard():
    if g.user["role"] == "petugas":          # petugas langsung ke daftar ketetapannya
        return redirect(url_for("app.data_pajak"))
    conn = connect()
    try:
        tahun = tahun_aktual()
        S.perbarui_denda(conn)
        s = get_settings(conn)
        pid = None if g.user["role"] == "admin" else g.user.get("petugas_id")
        ring = S.ringkasan(conn, tahun, pid)
        antrean = S.daftar_antrean_billing(conn, tahun)
        bulan_ini = f"{dt.date.today():%Y-%m}-01"
        setor_hari_ini = conn.execute(
            "SELECT COUNT(*) n, COALESCE(SUM(jumlah_diterima),0) rp FROM pembayaran "
            "WHERE void=0 AND DATE(tgl_bayar)=?", (dt.date.today().isoformat(),)).fetchone()
        recent = [dict(r) for r in conn.execute(
            """SELECT b.*, t.nop, t.nama_wp, p.blok FROM pembayaran b
               JOIN tagihan t ON t.id=b.tagihan_id LEFT JOIN petugas p ON p.id=b.petugas_id
               WHERE b.void=0 ORDER BY b.id DESC LIMIT 8""")]
        bln = conn.execute(
            "SELECT COALESCE(SUM(jumlah_diterima),0) rp FROM pembayaran WHERE void=0 "
            "AND DATE(tgl_bayar) >= ?", (bulan_ini,)).fetchone()["rp"]
        sink = S.sinkron_state()
        return render_template("dashboard.html", tahun=tahun, ring=ring, antrean=len(antrean),
                               setor_hari=setor_hari_ini, bulan_rp=bln, recent=recent,
                               sinkron=sink, sinkron_log=sink["log"][-40:], settings=s,
                               tahun_list=S.daftar_tahun(conn))
    finally:
        conn.close()


@bp.route("/data")
def data_pajak():
    conn = connect()
    try:
        tahun = tahun_aktual()
        S.perbarui_denda(conn)          # denda otomatis mengikuti bulan berjalan
        where, args = ["t.aktif = 1", "t.tahun_pajak = ?"], [tahun]
        q = arg("cari")
        if q:
            if F.norm_nop(q):
                where.append("t.nop = ?")
                args.append(F.norm_nop(q))
            else:
                where.append("(t.nama_wp LIKE ? OR t.alamat_objek LIKE ? OR t.nop LIKE ?)")
                args += [f"%{q}%"] * 3
        if arg("petugas"):
            where.append("t.petugas_id = ?")
            args.append(arg("petugas"))
        st = arg("status")
        if st == "belum":               # belum ada pembayaran aktif sama sekali
            where.append("NOT EXISTS (SELECT 1 FROM pembayaran b "
                         "WHERE b.tagihan_id = t.id AND b.void = 0)")
        elif st in ("lunas", "proses"):
            sub = {"lunas": "AND b.status='terverifikasi'",
                   "proses": "AND b.status<>'terverifikasi'"}
            where.append("(EXISTS (SELECT 1 FROM pembayaran b WHERE b.tagihan_id=t.id AND b.void=0 "
                         + sub[st] + "))")
        pid = None if g.user["role"] == "admin" else g.user.get("petugas_id")
        if pid:
            where.append("t.petugas_id = ?")
            args.append(pid)
        sortir = arg("sortir")
        ORDER = {
            "": "st_urut, t.nop",                                  # belum bayar didahulukan
            "nop": "t.nop",
            "nama": "t.nama_wp COLLATE NOCASE, t.nop",
            "besar": "(t.pokok + t.denda) DESC, t.nop",
            "kecil": "(t.pokok + t.denda), t.nop",
            "denda": "t.denda DESC, (t.pokok + t.denda) DESC, t.nop",
            "baru": "t.id DESC",
        }
        rows = [dict(r) for r in conn.execute(
            S.tagihan_view_sql("WHERE " + " AND ".join(where), ORDER.get(sortir, ORDER[""])), args)]
        belum_n = proses_n = lunas_n = 0
        tagih_rp = denda_rp = 0
        ada_denda = False
        for r in rows:
            r["status"] = S.status_tagihan(r)
            total_rp = (r["pokok"] or 0) + (r["denda"] or 0)
            if r["denda"]:
                ada_denda = True
            if r["status"] == "belum_bayar":
                belum_n += 1
                tagih_rp += total_rp
                denda_rp += r["denda"] or 0
            elif r["status"] == "proses":
                proses_n += 1
                tagih_rp += max(total_rp - (r["dibayar"] or 0), 0)
                denda_rp += r["denda"] or 0
            else:
                lunas_n += 1
        rows, page, pages, total = paginate(rows)
        return render_template("bills.html", rows=rows, tahun=tahun, petugas=S.daftar_petugas(conn),
                               page=page, pages=pages, total=total, q=q, st=st,
                               pg=arg("petugas"), sortir=sortir,
                               belum_n=belum_n, proses_n=proses_n, lunas_n=lunas_n,
                               tagih_rp=tagih_rp, denda_rp=denda_rp, ada_denda=ada_denda)
    finally:
        conn.close()


@bp.route("/cari")
def cari():
    """Jalan pintas petugas: ketik NOP / nama -> langsung ke halaman rekam bayar."""
    q = arg("nop") or arg("cari")
    tahun = arg("tahun") or ""
    if not q:
        return redirect(url_for("app.data_pajak"))
    rows = []
    nop = F.norm_nop(q)
    conn = connect()
    try:
        where = ["t.aktif = 1"]
        args: list = []
        if nop:
            where.append("t.nop = ?")
            args.append(nop)
        else:
            where.append("(t.nama_wp LIKE ? OR t.nop LIKE ? OR t.alamat_objek LIKE ?)")
            args += [f"%{q}%"] * 3
        if tahun:
            where.append("t.tahun_pajak = ?")
            args.append(tahun)
        pid = None if g.user["role"] == "admin" else g.user.get("petugas_id")
        if pid:
            where.append("(t.petugas_id = ? OR t.petugas_id IS NULL)")
            args.append(pid)
        rows = [dict(r) for r in conn.execute(S.tagihan_view_sql("WHERE " + " AND ".join(where)), args)]
    finally:
        conn.close()
    for r in rows:
        r["status"] = S.status_tagihan(r)
    if not rows:
        flash(f"NOP/kartu pencarian “{q}” tidak ada dalam data ketetapan tahun {tahun or 'berapa pun'}.",
              "warn")
        return redirect(url_for("app.data_pajak", cari=q))
    if len(rows) == 1:      # satu-satunya kecocokan: langsung buka halamannya
        t = rows[0]
        if t["pembayaran_id"]:
            return redirect(url_for("app.kwitansi", pid=t["pembayaran_id"]))
        return redirect(url_for("app.bayar", tag_id=t["id"]))
    return render_template("cari_hasil.html", rows=rows, q=q, tahun=tahun)


@bp.route("/data/detail/<int:tag_id>")
def tagihan_detail(tag_id: int):
    conn = connect()
    try:
        row = conn.execute(S.tagihan_view_sql("WHERE t.id = ?"), (tag_id,)).fetchone()
        if not row:
            abort(404)
        row = dict(row)
        row["status"] = S.status_tagihan(row)
        bayar = [dict(r) for r in conn.execute(
            "SELECT b.*, p.blok, p.nama AS petugas_nama FROM pembayaran b "
            "LEFT JOIN petugas p ON p.id=b.petugas_id WHERE b.tagihan_id=? ORDER BY b.id DESC", (tag_id,))]
        real = [dict(r) for r in conn.execute(
            "SELECT * FROM realisasi WHERE nop=? AND tahun_pajak=? ORDER BY tgl_bayar DESC LIMIT 10",
            (row["nop"], row["tahun_pajak"]))]
        log = [dict(r) for r in conn.execute(
            "SELECT l.* FROM billing_log l JOIN pembayaran b ON b.id=l.pembayaran_id "
            "WHERE b.tagihan_id=? ORDER BY l.id DESC LIMIT 15", (tag_id,))]
        return render_template("bill_detail.html", t=row, bayar=bayar, real=real, log=log)
    finally:
        conn.close()


@bp.route("/data/edit/<int:tag_id>", methods=["POST"])
@admin_perlu
def tagihan_edit(tag_id: int):
    conn = connect()
    try:
        pid = request.form.get("petugas_id") or None
        conn.execute("UPDATE tagihan SET pokok=?, denda=?, petugas_id=?, keterangan=?, updated_at=? "
                     "WHERE id=?",
                     (int(re.sub(r"\D", "", request.form.get("pokok", "0")) or "0"),
                      int(re.sub(r"\D", "", request.form.get("denda", "0")) or "0"),
                      int(pid) if pid else None, request.form.get("keterangan", ""),
                      dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), tag_id))
        conn.commit()
        flash("Data ketetapan diperbarui.", "ok")
        return redirect(url_for("app.tagihan_detail", tag_id=tag_id))
    finally:
        conn.close()


@bp.route("/data/nonaktif/<int:tag_id>", methods=["POST"])
@admin_perlu
def tagihan_nonaktif(tag_id: int):
    conn = connect()
    try:
        conn.execute("UPDATE tagihan SET aktif=0 WHERE id=?", (tag_id,))
        conn.commit()
        flash("NOP ditandai nonaktif (mis. mutasi/hapus).", "ok")
        return redirect(request.referrer or url_for("app.data_pajak"))
    finally:
        conn.close()


# ------------------------------------------------------------------ pembayaran

@bp.route("/bayar/<int:tag_id>", methods=["GET", "POST"])
def bayar(tag_id: int):
    conn = connect()
    try:
        tag = conn.execute("SELECT t.*, p.blok, p.nama AS petugas_nama FROM tagihan t "
                           "LEFT JOIN petugas p ON p.id=t.petugas_id WHERE t.id=?", (tag_id,)).fetchone()
        if not tag:
            abort(404)
        pid = g.user["petugas_id"] if g.user["role"] != "admin" else None
        if pid and tag["petugas_id"] != pid:
            abort(403, description="NOP ini bukan tagihan blok Anda.")
        if request.method == "POST":
            f = request.form
            jumlah = int(re.sub(r"\D", "", f.get("jumlah", "") or "0") or 0)
            data = {
                "jumlah": jumlah or (tag["pokok"] + tag["denda"]),
                "tgl_bayar": f.get("tgl_bayar") or dt.date.today().isoformat(),
                "metode": f.get("metode") or "petugas",
                "pembayar": f.get("pembayar") or tag["nama_wp"],
                "catatan": f.get("catatan", ""),
            }
            try:
                res = S.catat_pembayaran(conn, tag_id, data, int(f["petugas_id"]) if f.get("petugas_id") else pid)
                conn.commit()
                flash(f"Transaksi tersimpan. Nomor kwitansi {res['nomor_kwitansi'] or '-'}", "ok")
                return redirect(url_for("app.kwitansi", pid=res["id"]))
            except ValueError as e:
                flash(str(e), "danger")
                return redirect(url_for("app.tagihan_detail", tag_id=tag_id))
        existing = conn.execute("SELECT id, nomor_kwitansi FROM pembayaran WHERE tagihan_id=? AND void=0 "
                                "ORDER BY id DESC LIMIT 1", (tag_id,)).fetchone()
        if existing:
            return redirect(url_for("app.kwitansi", pid=existing["id"]))
        return render_template("payment_form.html", t=dict(tag), petugas=S.daftar_petugas(conn, True),
                               pid=pid, hari_ini=dt.date.today().isoformat(),
                               tagihan_total=tag["pokok"] + tag["denda"])
    finally:
        conn.close()


@bp.route("/transaksi")
def transaksi():
    conn = connect()
    try:
        tahun = tahun_aktual()
        where = ["t.tahun_pajak = ?"]
        args = [tahun]
        if g.user["role"] != "admin":
            where.append("b.petugas_id = ?")
            args.append(g.user.get("petugas_id") or 0)
        if arg("metode"):
            where.append("b.metode = ?")
            args.append(arg("metode"))
        if arg("status"):
            where.append("b.status = ?")
            args.append(arg("status"))
        if arg("dari"):
            where.append("DATE(b.tgl_bayar) >= ?")
            args.append(arg("dari"))
        if arg("sampai"):
            where.append("DATE(b.tgl_bayar) <= ?")
            args.append(arg("sampai"))
        rows = [dict(r) for r in conn.execute(
            f"""SELECT b.*, t.nop, t.nama_wp, t.pokok, t.denda, t.alamat_objek, p.blok, p.nama AS petugas_nama
                 FROM pembayaran b JOIN tagihan t ON t.id=b.tagihan_id
                 LEFT JOIN petugas p ON p.id=b.petugas_id
                 WHERE {' AND '.join(where)} ORDER BY b.tgl_bayar DESC, b.id DESC""", args)]
        total = sum(r["jumlah_diterima"] for r in rows)
        if request.args.get("format") == "csv":
            return csv_rows(rows, ["tanggal", "nomor_kwitansi", "nop", "nama_wp", "blok", "jumlah_diterima",
                                   "metode", "status", "billing_code", "pembayar"])
        return render_template("payments.html", rows=rows[:800], total=total, tahun=tahun,
                               STATUS_LABEL=STATUS_LABEL, arg_dari=arg("dari"), arg_sampai=arg("sampai"),
                               metode=arg("metode"), status_s=arg("status"))
    finally:
        conn.close()


def csv_rows(rows: list[dict], cols: list[str]):
    buf = io.StringIO()
    buf.write(";".join(cols) + "\n")
    for r in rows:
        buf.write(";".join(str(r.get(c, "")) for c in cols) + "\n")
    bio = io.BytesIO(buf.getvalue().encode("utf-8-sig"))
    return send_file(bio, as_attachment=True, download_name=f"pbb-{dt.date.today():%Y%m%d}.csv",
                     mimetype="text/csv")


@bp.route("/transaksi/batal/<int:pid>", methods=["POST"])
def batalkan(pid: int):
    conn = connect()
    try:
        row = conn.execute("SELECT b.*, t.petugas_id tp FROM pembayaran b JOIN tagihan t ON t.id=b.tagihan_id "
                           "WHERE b.id=?", (pid,)).fetchone()
        if not row:
            abort(404)
        if g.user["role"] != "admin" and row["petugas_id"] != g.user.get("petugas_id"):
            abort(403)
        S.batalkan_pembayaran(conn, pid)
        conn.commit()
        flash("Transaksi dibatalkan. Kwitansi lama tidak berlaku.", "warn")
        return redirect(request.referrer or url_for("app.transaksi"))
    finally:
        conn.close()


@bp.route("/transaksi/edit/<int:pid>", methods=["POST"])
@admin_perlu
def transaksi_edit(pid: int):
    conn = connect()
    try:
        f = request.form
        conn.execute("UPDATE pembayaran SET tgl_bayar=?, metode=?, jumlah_diterima=?, pembayar=?, catatan=?, "
                     "status=?, billing_code=?, updated_at=? WHERE id=?",
                     (f.get("tgl_bayar"), f.get("metode"), int(re.sub(r"\D", "", f.get("jumlah", "0")) or 0),
                      f.get("pembayar", ""), f.get("catatan", ""), f.get("status", "terkirim"),
                      f.get("billing_code", ""), dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), pid))
        conn.commit()
        flash("Data transaksi diperbarui.", "ok")
        return redirect(url_for("app.kwitansi", pid=pid))
    finally:
        conn.close()


# ------------------------------------------------------------------- kwitansi

def _ambil_pembayaran(conn, pid: int):
    row = conn.execute(
        """SELECT b.*, t.nop, t.nama_wp, t.alamat_objek, t.alamat_wp, t.pokok, t.denda, t.tahun_pajak,
                  t.luas_bumi, t.luas_bangunan
           FROM pembayaran b JOIN tagihan t ON t.id=b.tagihan_id WHERE b.id=?""", (pid,)).fetchone()
    if not row:
        abort(404)
    row = dict(row)
    if g.user["role"] != "admin" and row["petugas_id"] != g.user.get("petugas_id"):
        abort(403, description="Kwitansi ini bukan milik blok Anda.")
    pt = conn.execute("SELECT * FROM petugas WHERE id=?", (row["petugas_id"] or 0,)).fetchone()
    return row, (dict(pt) if pt else None)


@bp.route("/kwitansi/<int:pid>")
def kwitansi(pid: int):
    conn = connect()
    try:
        row, pt = _ambil_pembayaran(conn, pid)
        d = format_kwitansi(row, get_settings(conn), pt)
        return render_template("kwitansi_print.html", d=d, row=row, pt=pt,
                               STATUS_LABEL=STATUS_LABEL)
    finally:
        conn.close()


@bp.route("/kwitansi/<int:pid>.pdf")
def kwitansi_pdf(pid: int):
    conn = connect()
    try:
        row, pt = _ambil_pembayaran(conn, pid)
        out = DATA_DIR / "cetak" / f"kwitansi-{(row['nomor_kwitansi'] or pid)}.pdf".replace("/", "-")
        buat_pdf(row, get_settings(conn), out, pt)
        return send_file(out, as_attachment=bool(arg("unduh")), download_name=out.name,
                         mimetype="application/pdf")
    finally:
        conn.close()


@bp.route("/cek")
@bp.route("/cek/<path:kode>")
def cek_kwitansi_publik(kode: str = ""):
    """Cek keaslian kwitansi tanpa login: /cek/09-2026-001, /cek/PBB-1234 atau /cek?nomor=09/2026/001."""
    q = (kode or request.args.get("nomor", "") or "").strip()
    if not q:
        return render_template("cek.html", ok=False, q="", d=None, row=None)
    q = q.replace(" ", "")
    conn = connect()
    try:
        rows = [dict(r) for r in conn.execute(
            """SELECT b.*, t.nop, t.nama_wp, t.tahun_pajak, p.blok, p.nama AS petugas_nama,
                      p.nip AS petugas_nip
               FROM pembayaran b JOIN tagihan t ON t.id=b.tagihan_id
               LEFT JOIN petugas p ON p.id=b.petugas_id
               WHERE COALESCE(b.nomor_kwitansi,'') <> '' ORDER BY b.id DESC LIMIT 4000""")]
        s = get_settings(conn)
        target = q.lower().replace(" ", "")
        found = next((r for r in rows if (r["nomor_kwitansi"] or "").lower().replace(" ", "") == target), None)
        if not found:
            found = next((r for r in rows
                          if format_kwitansi(r, s, None)["cek_kode"].lower() == target), None)
        if not found:
            return render_template("cek.html", ok=False, q=q), 404
        return render_template("cek.html", ok=not found["void"], q=q,
                               d=format_kwitansi(found, s, {"nama": found.get("petugas_nama"),
                                                             "nip": found.get("petugas_nip"),
                                                             "blok": found.get("blok")}),
                               row=found)
    finally:
        conn.close()


# -------------------------------------------------------------- impor / ekspor

@bp.route("/impor", methods=["GET", "POST"])
@admin_perlu
def impor():
    conn = connect()
    try:
        hasil = None
        if request.method == "POST":
            jenis = request.form.get("jenis", "ketetapan")
            tahun = request.form.get("tahun") or str(dt.date.today().year)
            try:
                path = simpan_upload(request.files.get("file"))
                rows, sheet = read_sheet(path, jenis)
                if not rows:
                    raise ValueError(
                        "Baris data tidak terbaca. Pastikan ada kolom 'NOP' dan baris header di "
                        "baris pertama. Sheet yang dibaca: " + str(sheet))
                if jenis == "ketetapan":
                    stat = S.impor_ketetapan(conn, rows, tahun, path.name, g.user["username"],
                                             bool(request.form.get("aktifkan_semua")))
                else:
                    stat = S.impor_realisasi(conn, rows, path.name, g.user["username"])
                conn.commit()
                stat["file"] = path.name
                stat["sheet"] = sheet
                hasil = stat
                flash(f"Impor {jenis} selesai: {stat.get('baru', stat.get('baris', 0))} baris diproses.", "ok")
            except Exception as e:
                hasil = {"error": f"{type(e).__name__}: {e}"}
                flash(str(e), "danger")
        tahun_default = str(dt.date.today().year)
        batch = [dict(r) for r in conn.execute(
            "SELECT * FROM import_batch ORDER BY id DESC LIMIT 8")]
        return render_template("import.html", hasil=hasil, tahun=tahun_default, batch=batch)
    finally:
        conn.close()


@bp.route("/unduh/format")
@admin_perlu
def unduh_format():
    """Berikan template Excel agar format file petugas sesuai."""
    yang = arg("jenis", "ketetapan")
    tahun = str(dt.date.today().year)
    if yang == "realisasi":
        wb = write_table(
            ["NOP", "NAMA WAJIB PAJAK", "ALAMAT WAJIB PAJAK", "TAHUN PAJAK", "POKOK", "DENDA",
             "JUMLAH PEMBAYARAN", "TANGGAL PEMBAYARAN", "CHANNEL"],
            [["35.18.010001.0001-0", "Slamet Wibowo", "Jl. Merdeka No.1", tahun, 28500, 0, 28500,
              dt.date.today().strftime("%d/%m/%Y"), "Bank Jatim"]],
            "FORMAT IMPOR DATA REALISASI (unduh dari eSPPT)",
            "Isi dari baris ke-2. Kolom NOP, TAHUN PAJAK dan JUMLAH PEMBAYARAN wajib ada.",
            money_cols=[5, 6, 7], widths=[24, 26, 30, 11, 12, 10, 14, 16, 14])
    else:
        wb = write_table(
            ["NOMOR", "NOP", "TAHUN PAJAK", "NAMA WAJIB PAJAK", "ALAMAT OBJEK PAJAK",
             "ALAMAT WAJIB PAJAK", "LUAS BUMI", "LUAS BANGUNAN", "PBB YG DIBAYAR", "NAMA PETUGAS BLOK"],
            [[1, "35.18.010001.0001-0", tahun, "Slamet Wibowo", "Jl. Merdeka No.1", "Jl. Merdeka No.1",
              210, 60, 28500, "Blok 1"],
             [2, "35.18.010001.0002-0", tahun, "Siti Aminah", "Jl. Kenanga No.7", "Jl. Kenanga No.7",
              96, 36, 41250, "Blok 2"]],
            "FORMAT IMPOR DATA KETETAPAN PBB-P2",
            "Kolom wajib: NOP, TAHUN PAJAK, NAMA WAJIB PAJAK, PBB YG DIBAYAR. "
            "Kolom NAMA PETUGAS BLOK dipakai memetakan 7 petugas.",
            money_cols=[9], widths=[7, 24, 11, 26, 30, 30, 10, 12, 15, 18])
    out = DATA_DIR / f"format-impor-{yang}.xlsx"
    save(wb, out)
    return send_file(out, as_attachment=True, download_name=out.name,
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# --------------------------------------------------------------------- laporan

@bp.route("/laporan")
@admin_perlu
def laporan():
    conn = connect()
    try:
        tahun = tahun_aktual()
        S.perbarui_denda(conn)
        dari, sampai = arg("dari"), arg("sampai")
        rows = S.laporan_blok(conn, tahun, "", dari, sampai)
        tot = {k: sum(r[k] for r in rows) for k in
               ("target_sppt", "lunas_sppt", "proses_sppt", "belum_sppt", "target_nilai", "lunas_nilai",
                "via_petugas", "via_petugas_rp", "bayar_sendiri", "bayar_sendiri_rp", "kwitansi",
                "kwitansi_rp", "di_tangan", "di_tangan_rp", "kekurangan_nilai")}
        tot["persen_sppt"] = F.persen(tot["lunas_sppt"], tot["target_sppt"])
        tot["persen_rp"] = F.persen(tot["lunas_nilai"], tot["target_nilai"])
        selisih = S.selisih_kwitansi_vs_setoran(conn, tahun, dari, sampai)
        tanpa = S.realisasi_tanpa_kwitansi(conn, tahun, dari, sampai)
        return render_template("reports.html", tahun=tahun, rows=rows, tot=tot, selisih=selisih,
                               tanpa=tanpa, dari=dari, sampai=sampai)
    finally:
        conn.close()


@bp.route("/laporan/realisasi.csv")
@admin_perlu
def laporan_realisasi_csv():
    conn = connect()
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM realisasi WHERE tahun_pajak=? ORDER BY tgl_bayar", (tahun_aktual(),))]
        return csv_rows(rows, ["nop", "nama_wp", "tahun_pajak", "pokok", "denda", "jumlah",
                              "tgl_bayar", "channel"])
    finally:
        conn.close()


@bp.route("/laporan/progres.xlsx")
@admin_perlu
def laporan_xlsx():
    conn = connect()
    try:
        tahun = tahun_aktual()
        rows = S.laporan_blok(conn, tahun, "", arg("dari"), arg("sampai"))
        data = [[r["blok"], r["nama"], r["target_sppt"], r["lunas_sppt"], r["proses_sppt"], r["belum_sppt"],
                 r["persen_sppt"], r["target_nilai"], r["lunas_nilai"], r["kekurangan_nilai"],
                 r["via_petugas_rp"], r["bayar_sendiri_rp"], r["di_tangan_rp"]] for r in rows]
        tot = ["TOTAL", "", sum(r["target_sppt"] for r in rows), sum(r["lunas_sppt"] for r in rows),
               sum(r["proses_sppt"] for r in rows), sum(r["belum_sppt"] for r in rows), "",
               sum(r["target_nilai"] for r in rows), sum(r["lunas_nilai"] for r in rows),
               sum(r["kekurangan_nilai"] for r in rows),
               sum(r["via_petugas_rp"] for r in rows), sum(r["bayar_sendiri_rp"] for r in rows),
               sum(r["di_tangan_rp"] for r in rows)]
        data.append(tot)
        wb = write_table(
            ["Blok", "Nama Petugas", "Jumlah SPPT", "Lunas", "Proses", "Belum Bayar", "% SPPT",
             "Target (Rp)", "Terealisasi (Rp)", "Kekurangan (Rp)", "Setoran Petugas (Rp)",
             "Bayar Mandiri (Rp)", "Uang Masih di Tangan (Rp)"],
            data, f"PROGRES PEMBAYARAN PBB-P2 TAHUN {tahun}",
            f"Direkap {dt.datetime.now():%d/%m/%Y %H:%M} — sumber: ketetapan + data realisasi eSPPT",
            money_cols=[8, 9, 10, 11, 12, 13], widths=[12, 24, 11, 8, 8, 11, 9, 15, 16, 16, 16, 16, 18])
        out = DATA_DIR / f"laporan-progres-{tahun}.xlsx"
        save(wb, out)
        return send_file(out, as_attachment=True, download_name=out.name,
                         mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    finally:
        conn.close()


@bp.route("/laporan/kwitansi")
def laporan_kwitansi():
    conn = connect()
    try:
        tahun = tahun_aktual()
        pid = None if g.user["role"] == "admin" else g.user.get("petugas_id")
        rows = S.rekap_kwitansi(conn, tahun, pid, arg("dari"), arg("sampai"))
        total = sum(r["jumlah_diterima"] for r in rows)
        if request.args.get("format") == "csv":
            return csv_rows(rows, ["nomor_kwitansi", "tgl_bayar", "nop", "nama_wp", "jumlah_diterima",
                                   "status", "billing_code"])
        return render_template("kwitansi_rekap.html", rows=rows, total=total, tahun=tahun,
                               STATUS_LABEL=STATUS_LABEL, arg_dari=arg("dari"), arg_sampai=arg("sampai"))
    finally:
        conn.close()


@bp.route("/laporan/setoran-ke-kas")
@admin_perlu
def laporan_setoran():
    """Rekap uang yang harus disetor petugas ke kas daerah (belum lunas di eSPPT)."""
    conn = connect()
    try:
        tahun = tahun_aktual()
        rows = [dict(r) for r in conn.execute(
            """SELECT b.*, t.nop, t.nama_wp, p.blok FROM pembayaran b JOIN tagihan t ON t.id=b.tagihan_id
               LEFT JOIN petugas p ON p.id=b.petugas_id
               WHERE b.void=0 AND COALESCE(b.nomor_kwitansi,'')<>'' AND t.tahun_pajak=?
                 AND b.status <> 'terverifikasi' ORDER BY p.blok, b.tgl_bayar""", (tahun,))]
        per = {}
        for r in rows:
            k = r["blok"] or "(belum diatur)"
            a = per.setdefault(k, {"blok": k, "n": 0, "rp": 0})
            a["n"] += 1
            a["rp"] += r["jumlah_diterima"]
        return render_template("setoran.html", rows=rows, per=sorted(per.values(), key=lambda x: x["blok"]),
                               total=sum(r["jumlah_diterima"] for r in rows), tahun=tahun)
    finally:
        conn.close()


# ------------------------------------------------------------------- antrean billing

@bp.route("/billing")
def billing():
    conn = connect()
    try:
        tahun = tahun_aktual()
        pid = None if g.user["role"] == "admin" else g.user.get("petugas_id")
        rows = S.daftar_antrean_billing(conn, tahun, pid)
        sudah = [dict(r) for r in conn.execute(
            """SELECT b.id, b.nomor_kwitansi, b.billing_code, b.tgl_billing, b.status, t.nop, t.nama_wp
               FROM pembayaran b JOIN tagihan t ON t.id=b.tagihan_id
               WHERE b.void=0 AND COALESCE(b.billing_code,'')<>'' ORDER BY b.tgl_billing DESC LIMIT 40""")]
        s = get_settings(conn)
        return render_template("billing.html", rows=rows, sudah=sudah, tahun=tahun, s=s,
                               sinkron=S.sinkron_state())
    finally:
        conn.close()


@bp.route("/billing/buat", methods=["GET", "POST"])
@admin_perlu
def billing_buat():
    ids = [int(x) for x in request.form.getlist("id") if str(x).isdigit()]
    conn = connect()
    try:
        try:
            res = S.jalankan_billing(conn, ids, g.user["username"])
            conn.commit()
            flash(f"Billing dibuat {res['sukses']} NOP, gagal {res['gagal']}.",
                  "ok" if res["sukses"] and not res["gagal"] else "warn")
        except Exception as e:
            conn.rollback()
            flash(str(e), "danger")
        return redirect(url_for("app.billing"))
    finally:
        conn.close()


@bp.route("/billing/tempel", methods=["GET", "POST"])
def billing_tempel():
    """Cara cepat & aman: tempel daftar 'NOP + kode billing' dari eSPPT/Excel.

    Baris boleh berisi nomor urut, tab, titik koma, atau teks pendahulu — yang penting ada
    NOP dan angka panjang (kode billing). Kode = deretan digit TERAKHIR pada baris.
    """
    text = request.form.get("tempel") or ""
    conn = connect()
    try:
        ok, gagal, examples = 0, 0, []
        for line in re.split(r"[\r\n]+", text):     # titik koma DIPERBOLEHKAN sebagai pemisah NOP/kode
            line = line.strip()
            if not line:
                continue
            nop = ""
            for tok in re.split(r"[\s,\t|]+", line):
                cand = F.norm_nop(tok)
                if cand:
                    nop = cand
                    break
            run = [m for m in re.finditer(r"\d{8,}", line)]
            code = ""
            for m in reversed(run):
                d = m.group(0)
                if nop and d == F.nop_polos(nop):
                    continue
                code = d
                break
            if not nop or not code:
                gagal += 1
                examples.append(line[:70])
                continue
            row = conn.execute(
                """SELECT b.id FROM pembayaran b JOIN tagihan t ON t.id=b.tagihan_id
                   WHERE t.nop=? AND b.void=0 ORDER BY b.id DESC LIMIT 1""", (nop,)).fetchone()
            if not row:
                gagal += 1
                examples.append(f"{nop} (belum ada transaksi tercatat)")
                continue
            conn.execute("UPDATE pembayaran SET billing_code=?, tgl_billing=?, status=CASE WHEN "
                         "status='terverifikasi' THEN status ELSE 'terkirim' END, updated_at=? WHERE id=?",
                         (code, dt.date.today().isoformat(),
                          dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), row["id"]))
            ok += 1
        conn.commit()
        pesan = f"Kode billing tercatat untuk {ok} NOP."
        if gagal:
            pesan += (f" {gagal} baris tidak dikenali: " + " · ".join(examples[:3]))
        flash(pesan, "ok" if ok else "warn")
        return redirect(url_for("app.billing"))
    finally:
        conn.close()


@bp.route("/realisasi")
@admin_perlu
def realisasi():
    conn = connect()
    try:
        tahun = tahun_aktual()
        rows = [dict(r) for r in conn.execute(
            """SELECT r.*, t.id AS tagihan_id, t.nama_wp AS nama_ketetapan, p.blok
               FROM realisasi r LEFT JOIN tagihan t ON t.nop=r.nop AND t.tahun_pajak=r.tahun_pajak
               LEFT JOIN petugas p ON p.id=t.petugas_id
               WHERE r.tahun_pajak=? ORDER BY r.tgl_bayar DESC, r.id DESC LIMIT 300""", (tahun,))]
        return render_template("realisasi.html", rows=rows, tahun=tahun)
    finally:
        conn.close()


# ----------------------------------------------------------------- pengaturan

@bp.route("/petugas")
@admin_perlu
def petugas_page():
    conn = connect()
    try:
        rows = []
        for p in S.daftar_petugas(conn):
            n = conn.execute("SELECT COUNT(*) c FROM tagihan t WHERE t.petugas_id=? AND t.aktif=1",
                             (p["id"],)).fetchone()["c"]
            lunas = conn.execute(
                """SELECT COUNT(*) c FROM tagihan t JOIN pembayaran b ON b.tagihan_id=t.id AND b.void=0
                   WHERE t.petugas_id=? AND b.status='terverifikasi'""", (p["id"],)).fetchone()["c"]
            rows.append({**p, "jumlah": n, "lunas": lunas})
        users = [dict(r) for r in conn.execute(
            "SELECT id, username, nama, role, petugas_id, aktif FROM users ORDER BY role, username")]
        eid = int(arg("edit", "0") or 0)
        edit = conn.execute("SELECT * FROM petugas WHERE id=?", (eid,)).fetchone() if eid else None
        punya_user = {u["petugas_id"] for u in users if u["petugas_id"]}
        return render_template("petugas.html", rows=rows, users=users, edit=dict(edit) if edit else None,
                               punya_user=punya_user)
    finally:
        conn.close()


@bp.route("/petugas/simpan", methods=["POST"])
@admin_perlu
def petugas_simpan():
    conn = connect()
    try:
        f = request.form
        data = {"id": int(f["id"]) if f.get("id") else None, "nama": f.get("nama", "").strip(),
                "blok": f.get("blok", "").strip() or f.get("nama", "").strip(),
                "wilayah": f.get("wilayah", ""), "telepon": f.get("telepon", ""),
                "nip": f.get("nip", ""), "jabatan": f.get("jabatan", "Petugas Pemungut PBB-P2"),
                "aktif": f.get("aktif") or (not f.get("id"))}
        if not data["nama"]:
            flash("Nama petugas wajib diisi.", "danger")
        else:
            pid = S.save_petugas(conn, data)
            if f.get("buat_user") and pid:
                uname = F.slug(f.get("nama"))[:20]
                pw = f.get("password") or "pbb123"
                if conn.execute("SELECT id FROM users WHERE username=?", (uname,)).fetchone():
                    flash(f"Petugas disimpan. User '{uname}' sudah ada — akun tidak dibuat ulang "
                          "(reset passwordnya di tabel Akun login bila perlu).", "ok")
                else:
                    from werkzeug.security import generate_password_hash
                    conn.execute("INSERT INTO users(username,password_hash,nama,role,petugas_id,created_at) "
                                 "VALUES(?,?,?,?,?,?)",
                                 (uname, generate_password_hash(pw), f.get("nama"), "petugas", pid,
                                  dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
                    flash(f"Petugas disimpan. Akses login petugas: user '{uname}' password '{pw}'.", "ok")
            else:
                flash("Data petugas disimpan.", "ok")
            conn.commit()
        return redirect(url_for("app.petugas_page"))
    finally:
        conn.close()


@bp.route("/petugas/hapus", methods=["POST"])
@admin_perlu
def petugas_hapus():
    conn = connect()
    try:
        f = request.form
        try:
            pid = int(f.get("id", "0") or 0)
        except ValueError:
            pid = 0
        pilih = (f.get("pindah_ke", "") or "").strip()
        tujuan = None
        if pilih and pilih != "lepas":
            try:
                tujuan = int(pilih)
            except ValueError:
                flash("Pilihan petugas pengganti tidak sah.", "danger")
                return redirect(url_for("app.petugas_page"))
        elif not pilih:
            n = conn.execute("SELECT COUNT(*) c FROM tagihan WHERE petugas_id=?",
                             (pid,)).fetchone()["c"]
            if n:
                flash(f"Petugas masih memegang {n} NOP — pilih petugas pengganti "
                      "(atau 'lepas') pada kolom pilihan di sebelah tombol hapus, lalu hapus lagi.",
                      "danger")
                return redirect(url_for("app.petugas_page"))
        try:
            info = S.hapus_petugas(conn, pid, tujuan)
            conn.commit()
            msg = f"Petugas {info['nama']} (Blok {info['blok']}) dihapus."
            if info["n_nop"]:
                msg += (f" {info['n_nop']} NOP dipindah ke {info['tujuan']}." if info["tujuan"]
                        else f" {info['n_nop']} NOP dilepas (tanpa petugas).")
            if info["n_usulan_ditolak"]:
                msg += f" {info['n_usulan_ditolak']} usulan pindah menunggu ditolak otomatis."
            if info["users"]:
                msg += f" Akun login dinonaktifkan: {', '.join(info['users'])}."
            flash(msg, "ok")
        except ValueError as e:
            flash(str(e), "danger")
        return redirect(url_for("app.petugas_page"))
    finally:
        conn.close()


@bp.route("/petugas/user/reset", methods=["POST"])
@admin_perlu
def user_reset():
    from werkzeug.security import generate_password_hash

    uid = int(request.form.get("id") or 0)
    pw = request.form.get("password") or "pbb123"
    conn = connect()
    try:
        if not conn.execute("SELECT id FROM users WHERE id=?", (uid,)).fetchone():
            abort(404)
        conn.execute("UPDATE users SET password_hash=? WHERE id=?", (generate_password_hash(pw), uid))
        conn.commit()
        flash("Password user sudah direset. Minta petugas menggantinya setelah masuk.", "ok")
        return redirect(url_for("app.petugas_page"))
    finally:
        conn.close()


@bp.route("/petugas/user/<int:uid>/<aktif>", methods=["POST"])
@admin_perlu
def user_aktif(uid: int, aktif: str):
    conn = connect()
    try:
        conn.execute("UPDATE users SET aktif=? WHERE id=?", (1 if aktif == "nyala" else 0, uid))
        conn.commit()
        return redirect(url_for("app.petugas_page"))
    finally:
        conn.close()


@bp.route("/pindah")
def pindah_page():
    """Petugas mengajukan pindah blok WP; admin memutuskan (acc/tolak)."""
    conn = connect()
    try:
        pid = None if g.user["role"] == "admin" else g.user.get("petugas_id")
        tag = None
        tag_id = int(arg("tag", "0") or 0)
        if tag_id:
            row = conn.execute(S.tagihan_view_sql("WHERE t.id = ?"), (tag_id,)).fetchone()
            if row and (pid is None or row["petugas_id"] == pid):
                tag = dict(row)
        pilihan: list = []
        if pid:
            pilihan = [dict(r) for r in conn.execute(
                S.tagihan_view_sql("WHERE t.aktif = 1 AND t.petugas_id = ? "
                                   "AND NOT EXISTS (SELECT 1 FROM pembayaran b "
                                   "WHERE b.tagihan_id = t.id AND b.void = 0)", "t.nop"),
                (pid,))]
        usulan = S.daftar_usulan(conn, "", pid)
        n_pending = 0 if pid else sum(1 for u in usulan if u["status"] == "menunggu")
        return render_template("pindah.html", usulan=usulan, petugas=S.daftar_petugas(conn, True),
                               tag=tag, pilihan=pilihan, n_pending=n_pending)
    finally:
        conn.close()


@bp.route("/pindah/ajukan", methods=["POST"])
def pindah_ajukan():
    conn = connect()
    try:
        f = request.form
        try:
            tag_id = int(f.get("tagihan_id", "0") or 0)
            petugas_baru = int(f.get("petugas_baru", "0") or 0)
        except ValueError:
            flash("Pilihan NOP / blok tujuan tidak sah.", "danger")
            return redirect(url_for("app.pindah_page"))
        pid = None if g.user["role"] == "admin" else g.user.get("petugas_id")
        if pid:
            tag = conn.execute("SELECT petugas_id FROM tagihan WHERE id=?", (tag_id,)).fetchone()
            if not tag or tag["petugas_id"] != pid:
                flash("NOP itu bukan tanggungan blok Anda.", "danger")
                return redirect(url_for("app.pindah_page"))
        try:
            S.ajukan_pindah(conn, tag_id, petugas_baru, f.get("alasan", "").strip(), g.user["nama"])
            conn.commit()
            flash("Usulan pindah blok dikirim — menunggu persetujuan admin.", "ok")
        except ValueError as e:
            flash(str(e), "danger")
        return redirect(url_for("app.pindah_page"))
    finally:
        conn.close()


@bp.route("/pindah/putuskan/<int:usulan_id>", methods=["POST"])
@admin_perlu
def pindah_putuskan(usulan_id: int):
    conn = connect()
    try:
        try:
            st = S.putuskan_pindah(conn, usulan_id, request.form.get("keputusan") == "setuju",
                                   request.form.get("catatan", "").strip())
            conn.commit()
            flash("Usulan " + ("disetujui — NOP pindah ke blok baru."
                               if st == "disetujui" else "ditolak."), "ok")
        except ValueError as e:
            flash(str(e), "danger")
        return redirect(url_for("app.pindah_page"))
    finally:
        conn.close()


@bp.route("/pengaturan", methods=["GET", "POST"])
@admin_perlu
def pengaturan():
    conn = connect()
    try:
        if request.method == "POST":
            grup = request.form.get("grup")
            data = {}
            if grup == "umum":
                keys = ["wilayah_desa", "wilayah_kecamatan", "wilayah_kabupaten", "nama_petugas_bendahara",
                        "nip_petugas", "jabatan_penandatangan", "nama_lurah", "nip_lurah", "catatan_kwitansi",
                        "no_rekening"]
                data = {k: request.form.get(k, "").strip() for k in keys}
                data["cuti"] = str(int(re.sub(r"\D", "", request.form.get("cuti", "1")) or 1))
                data["auto_folder"] = "1" if request.form.get("auto_folder") else "0"
                data["auto_token"] = request.form.get("auto_token", "").strip()
            elif grup == "esppt":
                for k in ("esppt_base_url", "esppt_user", "esppt_password", "esppt_login_path",
                          "esppt_tagihan_path", "esppt_realisasi_path", "esppt_realisasi_params",
                          "esppt_realisasi_regex", "esppt_billing_template", "esppt_billing_regex",
                          "esppt_http_timeout", "esppt_delay_ms"):
                    data[k] = request.form.get(k, "").strip()
                data["esppt_enabled"] = "1" if request.form.get("esppt_enabled") else "0"
                data["esppt_verify_ssl"] = "1" if request.form.get("esppt_verify_ssl") else "0"
                data["esppt_billing_dry_run"] = "1" if request.form.get("esppt_billing_dry_run") else "0"
                data["skip_billing"] = "1" if request.form.get("skip_billing") else "0"
                data["auto_lunas_bayar_sendiri"] = "1" if request.form.get("auto_lunas_bayar_sendiri") else "0"
            elif grup == "denda":
                jt = re.sub(r"\D", "", request.form.get("jatuh_tempo_bulan", "8")) or "8"
                data["jatuh_tempo_bulan"] = str(min(max(int(jt), 1), 12))
                persen = re.sub(r"[^0-9.,]", "", request.form.get("denda_persen", "2")).replace(",", ".") or "2"
                try:
                    data["denda_persen"] = str(float(persen))
                except ValueError:
                    data["denda_persen"] = "2"
                maks = re.sub(r"\D", "", request.form.get("denda_maks_bulan", "24")) or "24"
                data["denda_maks_bulan"] = str(max(int(maks), 0))
            else:
                abort(400)
            set_settings(conn, data)
            conn.commit()
            refresh_settings_cache()
            n_denda = S.perbarui_denda(conn)      # langsung terpakai untuk tampilan berikutnya
            flash("Pengaturan disimpan." + (f" Denda {n_denda} NOP diperbarui." if grup == "denda" else ""), "ok")
            return redirect(url_for("app.pengaturan") + "?tab=" + grup)
        s = get_settings(conn)
        log = [dict(r) for r in conn.execute("SELECT * FROM billing_log ORDER BY id DESC LIMIT 25")]
        cadangan = [{"name": p.name, "stat_size": p.stat().st_size}
                    for p in sorted(BACKUP_DIR.glob("pbb-*.db"), reverse=True)[:10]]
        folder_auto = DATA_AUTO_DIR
        jumlah_auto = len([p for p in folder_auto.glob("*") if p.is_file()])
        return render_template("settings.html", s=s, log=log, tab=arg("tab", "umum"),
                               db_path=str(DB_PATH), size=db_size_bytes(), cadangan=cadangan,
                               folder_auto=str(folder_auto), jumlah_auto=jumlah_auto)
    finally:
        conn.close()


@bp.route("/pengaturan/uji", methods=["GET", "POST"])
@admin_perlu
def pengaturan_uji():
    langkah = request.form.get("langkah", "semua")
    conn = connect()
    try:
        s = get_settings(conn)
        client = EspptClient(s, log=lambda *a: None)
        hasil = []

        def tambah(nama, ok, pesan, contoh=""):
            hasil.append({"nama": nama, "ok": bool(ok), "pesan": pesan, "contoh": contoh[:600]})

        r = client.http(client.base)
        tambah("Koneksi ke server eSPPT", r.ok or r.status,
               f"HTTP {r.status} dari {client.base}" if r.ok else (r.error or "tidak terhubung"),
               (r.text or "")[:400] if r.ok else "")
        if langkah in ("semua", "login") and client.user:
            lr = client.login(force=True)
            tambah("Login Petugas PBB", lr.ok, lr.error or ("Berhasil: " + client.login_info), (lr.text or "")[:400])
        if langkah in ("semua", "realisasi"):
            rr = client.ambil_realisasi(str(dt.date.today().year))
            rows = EspptClient.parse_realisasi(rr) if rr.ok else []
            tambah("Ambil data realisasi", rr.ok and bool(rows),
                   f"terbaca {len(rows)} baris" if rows else (rr.error or f"HTTP {rr.status}; perlu pengaturan path/regex"),
                   (rr.text or "")[:500] if not rows else json.dumps(rows[:2], ensure_ascii=False))
        if langkah in ("semua", "billing"):
            b = client.buat_billing("35.18.010001.0001-0", str(dt.date.today().year))
            tambah("Uji buat billing (NOP contoh)", b.ok, b.error or "kode billing: " + (b.text or "")[:120],
                   (b.text or "")[:300])
        return render_template("uji.html", hasil=hasil, langkah=langkah)
    finally:
        conn.close()


@bp.route("/api/sinkron/start", methods=["POST"])
@admin_perlu
def api_sinkron_start():
    started = S.mulai_sinkron(
        {"tahun": tahun_aktual(), "tgl_dari": request.values.get("dari", ""),
         "tgl_sampai": request.values.get("sampai", ""),
         "buat_billing": request.values.get("billing", "1") == "1",
         "tarik_realisasi": request.values.get("realisasi", "1") == "1"},
        mode="manual")
    if not started:
        return jsonify({"ok": False, "pesan": "Sinkronisasi sedang berjalan."}), 409
    return jsonify({"ok": True})


@bp.route("/api/sinkron/status")
def api_sinkron_status():
    st = S.sinkron_state()
    st["total_rp"] = 0
    return jsonify(st)


@bp.route("/data/auto", methods=["GET", "POST"])
@admin_perlu
def data_auto():
    """Proses file yang ditaruh di data/auto/ (dipakai juga oleh jadwal latar belakang)."""
    conn = connect()
    try:
        hasil = S.proses_folder_auto(conn, g.user["username"] if g.get("user") else "jadwal")
        isi = [p.name for p in sorted((DATA_DIR / "auto").glob("*")) if p.is_file()]
        arsip = [p.name for p in sorted((DATA_DIR / "auto" / "sudah-diproses").glob("*"))][-10:]
        if request.method == "POST":
            if hasil:
                flash(" · ".join(f"{h['file']}: " + ("ok" if h.get("ok") else h.get("error", "gagal"))
                                 for h in hasil), "ok" if all(h.get("ok") for h in hasil) else "warn")
            else:
                flash("Tidak ada file di data/auto/ yang perlu diproses.", "info")
            return redirect(url_for("app.impor"))
        return jsonify({"diproses": hasil, "menunggu": isi, "arsip": arsip,
                        "folder": str(DATA_DIR / "auto")})
    finally:
        conn.close()


@bp.route("/data/auto/berkala")
def data_auto_berkala():
    """Titik panggil untuk penjadwal eksternal (Task Scheduler/cron): tanpa login, hanya token."""
    token_benar = (get_settings().get("auto_token") or "").strip()
    if token_benar and request.args.get("token") != token_benar:
        abort(403, description="token salah")
    conn = connect()
    try:
        hasil = S.proses_folder_auto(conn, "jadwal")
        return jsonify({"diproses": hasil})
    finally:
        conn.close()


@bp.route("/data/buang-percobaan", methods=["POST"])
@admin_perlu
def buang_percobaan():
    conn = connect()
    try:
        conn.execute("DELETE FROM billing_log")
        conn.execute("DELETE FROM pembayaran")
        conn.execute("DELETE FROM realisasi")
        conn.execute("DELETE FROM import_batch")
        conn.commit()
        flash("Data pembayaran & realisasi contoh sudah dibuang. Data ketetapan tetap aman.", "ok")
        return redirect(url_for("app.data_pajak"))
    finally:
        conn.close()


@bp.route("/data/cadangkan", methods=["POST"])
@admin_perlu
def cadangkan():
    p = daily_backup()
    flash("Cadangan dibuat: " + (p.name if p else "sudah ada cadangan hari ini."), "ok")
    return redirect(url_for("app.pengaturan"))


@bp.route("/data/cadangan/<nama>")
@admin_perlu
def unduh_cadangan(nama: str):
    p = (BACKUP_DIR / Path(nama).name).resolve()
    if BACKUP_DIR.resolve() not in p.parents or not p.exists():
        abort(404)
    return send_file(p, as_attachment=True, download_name=p.name)


@bp.route("/monitor")
def monitor():
    conn = connect()
    try:
        tahun = tahun_aktual()
        S.perbarui_denda(conn)
        rows = S.laporan_blok(conn, tahun)
        tot = {k: sum(r[k] for r in rows) for k in ("target_sppt", "lunas_sppt", "proses_sppt",
                                                     "belum_sppt", "target_nilai", "lunas_nilai",
                                                     "kekurangan_nilai")}
        tot["persen_sppt"] = F.persen(tot["lunas_sppt"], tot["target_sppt"])
        return render_template("monitor.html", tahun=tahun, rows=rows, tot=tot,
                               port=request.host.split(":")[-1] if ":" in request.host else "8088")
    finally:
        conn.close()


# ---------------------------------------------------------------------- factory

def create():
    from flask import Flask

    from jinja2 import ChoiceLoader, FileSystemLoader

    app = Flask(__name__, template_folder=str(BASE / "templates"), static_folder=str(BASE / "static"))
    # template boleh menyinggung berkas di static/ (mis. menyisipkan style.css langsung ke halaman)
    app.jinja_loader = ChoiceLoader([
        FileSystemLoader(str(BASE / "templates")),
        FileSystemLoader(str(BASE / "static")),
    ])
    keyfile = DATA_DIR / "secret_key"
    if not keyfile.exists():
        keyfile.write_text(secrets.token_hex(32))
    app.secret_key = os.environ.get("PBB_SECRET") or keyfile.read_text().strip()
    app.config.update(JSON_AS_MUTABLE=False, MAX_CONTENT_LENGTH=64 * 1024 * 1024,
                      SESSION_COOKIE_HTTPONLY=True)
    app.register_blueprint(auth_bp)
    app.register_blueprint(bp)
    init_auth(app)
    init_db(app)

    @app.context_processor
    def _globals():
        return dict(rp=F.rp, tg=F.tanggal, persen=F.persen, today=dt.date.today(),
                    s=_cache_settings.get("v") or get_settings(),
                    tahun_aktual_str=str(dt.date.today().year), STATUS=STATUS_LABEL,
                    status_label=lambda v: (STATUS_LABEL.get(v) or (v or "-", "muted"))[0],
                    status_kelas=lambda v: (STATUS_LABEL.get(v) or (v or "-", "muted"))[1],
                    tautan=lambda **kw: "?" + urlencode({**request.args.to_dict(), **{k: v for k, v in kw.items() if v not in (None, "")}}))

    @app.template_filter("rp")
    def _rp(v):
        return F.rp(v)

    @app.template_filter("tgl")
    def _tgl(v):
        return F.tanggal(v)

    @app.template_filter("tglhuruf")
    def _tlg(v):
        return F.tanggal(v, huruf=True)

    @app.template_filter("angka")
    def _a(v):
        return F.angka(v)

    @app.errorhandler(404)
    def _404(e):
        return render_template("error.html", code=404, pesan="Halaman/data tidak ditemukan."), 404

    @app.errorhandler(403)
    def _403(e):
        return render_template("error.html", code=403, pesan=(getattr(e, "description", "") or
                                                               "Anda tidak punya akses ke halaman ini.")), 403

    def _auto_loop():
        import time as _t

        _t.sleep(25)
        terakhir = 0.0
        while True:
            try:
                st = get_settings()
                if st.get("auto_folder", "1") in ("1", "true", "ya") and (time.time() - terakhir) > 6 * 3600:
                    conn = connect()
                    try:
                        hasil = S.proses_folder_auto(conn, "jadwal")
                    finally:
                        conn.close()
                    if hasil:
                        current_app.logger.info("data/auto diproses: %s", hasil)
                    terakhir = time.time()
            except Exception:                     # jadwal tidak boleh mematikan aplikasi
                current_app.logger.exception("jalur jadwal data/auto gagal")
                terakhir = time.time()
            _t.sleep(15 * 60)

    app.auto_loop = _auto_loop

    @app.teardown_appcontext
    def _backup(exception=None):
        try:
            daily_backup()
        except Exception:
            pass

    return app


def main() -> None:
    ap = argparse.ArgumentParser(description="Aplikasi Loket Pembayaran PBB-P2")
    ap.add_argument("--seed-demo", action="store_true", help="buat data contoh (7 blok)")
    ap.add_argument("--sync-once", action="store_true", help="jalankan sinkron eSPPT sekali")
    ap.add_argument("--port", type=int, default=int(os.environ.get("PBB_PORT", 8088)))
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--debug", action="store_true", help="pakai server pengembangan Flask (ada tombol reload)")
    args = ap.parse_args()

    app = create()
    if args.seed_demo:
        from tools.seed_demo import seed
        seed()
        return
    if args.sync_once:
        import time as _t

        ok = S.mulai_sinkron({"tahun": str(dt.date.today().year), "buat_billing": True}, mode="cli")
        print("Sinkron dimulai." if ok else "Sinkron sedang berjalan.")
        while S.sinkron_state()["berjalan"]:
            _t.sleep(1.5)
        st = S.sinkron_state()
        for line in st["log"]:
            print(" ", line["t"], f"[{line['level']}]", line["msg"])
        print("HASIL:", json.dumps(st["hasil"], ensure_ascii=False))
        return
    if not os.environ.get("PBB_NO_AUTO_LOOP"):
        threading.Thread(target=app.auto_loop, daemon=True).start()

    print(f"\n  Aplikasi Loket PBB siap: http://localhost:{args.port}   "
          f"(petugas: http://{{IP-komputer-anda}}:{args.port})")
    print("  Jendela ini adalah mesin aplikasi - selama terbuka, aplikasi jalan.\n")
    if args.debug:
        app.run(host=args.host, port=args.port, debug=True)
        return
    try:
        from waitress import serve

        serve(app, host=args.host, port=args.port, threads=8,
              connection_limit=200, recv_bytes=16384, send_bytes=16384,
              ident="loket-pbb", expose_tracebacks=False)
    except ImportError:
        app.run(host=args.host, port=args.port, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
