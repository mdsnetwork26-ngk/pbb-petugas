"""Cetak bukti bayar (kwitansi) — satu lembar A4 resmi dengan kop & tempat tanda tangan.

Dipakai untuk: halaman Pratinjau Cetak (render HTML) maupun unduhan PDF.
Keduanya memakai struktur data yang sama supaya tidak pernah beda.
"""
from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

from . import format as F

KOP1 = "PEMERINTAH {kab}"
KOP2 = "KECAMATAN {kec}"
KOP3 = "{desa}"
JUDUL = "KWITANSI PEMBAYARAN PAJAK BUMI DAN BANGUNAN PERKOTAAN/PERDESAAN (PBB-P2)"

GARIS = colors.HexColor("#1F4E79")
ABU = colors.HexColor("#666666")
BOX = colors.HexColor("#C7D2E0")


def _garis_label(c, y, x0, x1):
    c.setStrokeColor(colors.HexColor("#999999"))
    c.setDash(1, 1)
    c.line(x0, y, x1, y)
    c.setDash()


def format_kwitansi(row: dict, settings: dict, petugas: dict | None = None) -> dict:
    """Siapkan semua teks yang dibutuhkan template HTML & PDF."""
    hari = F.to_date(row.get("tgl_bayar")) or dt.date.today()
    ttd = petugas or {}
    return {
        "kop1": KOP1.format(kab=settings.get("wilayah_kabupaten", "")).upper(),
        "kop2": ("KECAMATAN " + re.sub(r"^KECAMATAN\s+", "", settings.get("wilayah_kecamatan", ""), flags=re.I)).upper(),
        "kop3": re.sub(r"^(KELURAHAN|DESA)\s+", lambda m: (m.group(1) + " ").upper(), settings.get("wilayah_desa", "").upper()),
        "judul": JUDUL,
        "nomor": row.get("nomor_kwitansi") or "-",
        "tgl_cetak": f"{F.HARI[hari.weekday()]} {hari.day} {F.BULAN[hari.month]} {hari.year}",
        "pembayar": row.get("pembayar") or row.get("nama_wp") or "-",
        "nop": row.get("nop") or "-",
        "tahun": row.get("tahun_pajak") or "-",
        "nama_wp": row.get("nama_wp") or "-",
        "alamat": row.get("alamat_objek") or row.get("alamat_wp") or "-",
        "pokok": F.rp(row.get("pokok") or 0),
        "pokok_rp": row.get("pokok") or 0,
        "denda": F.rp(row.get("denda") or 0),
        "denda_rp": row.get("denda") or 0,
        "jumlah": F.rp(row.get("jumlah_diterima") or 0),
        "jumlah_rp": row.get("jumlah_diterima") or 0,
        "terbilang": F.terbilang(row.get("jumlah_diterima") or 0).capitalize(),
        "metode": {"petugas": "Tunai melalui Petugas Pemungut", "bank": "Bank / ATM / Loket",
                   "pos": "Kantor Pos", "wp": "Dibayar sendiri oleh Wajib Pajak",
                   "lainnya": "Lainnya"}.get(row.get("metode"), row.get("metode") or "-"),
        "status": {"terverifikasi": "LUNAS (terverifikasi realisasi eSPPT)",
                   "terkirim": "Billing dibuat, menunggu pembayaran di bank/pos",
                   "menunggu_billing": "Uang diterima, menunggu pembuatan billing",
                   "belum_tagihan": "Uang diterima, belum diproses"}.get(row.get("status"), "-"),
        "billing": row.get("billing_code") or "",
        "blok": ttd.get("blok") or "",
        "petugas_nama": ttd.get("nama") or settings.get("nama_petugas_bendahara", "-"),
        "petugas_nip": ttd.get("nip") or settings.get("nip_petugas", ""),
        "petugas_jabatan": ttd.get("jabatan") or settings.get("jabatan_penandatangan", ""),
        "lurah_nama": settings.get("nama_lurah", "-"),
        "lurah_nip": settings.get("nip_lurah", ""),
        "catatan": settings.get("catatan_kwitansi", ""),
        "cek_kode": cek_kode(row),
        "no_rek": settings.get("no_rekening", ""),
    }


def cek_kode(row: dict) -> str:
    """Kode pengaman sederhana untuk cek keaslian kwitansi lewat menu 'Cek Kwitansi'."""
    base = f"{row.get('nomor_kwitansi')}|{row.get('nop')}|{row.get('jumlah_diterima')}"
    h = 0
    for ch in base:
        h = (h * 131 + ord(ch)) % 9000 + 1000
    return f"PBB-{h:04d}"


def buat_pdf(row: dict, settings: dict, path: Path, petugas: dict | None = None,
             tanda_tangan_gambar: str | None = None) -> Path:
    d = format_kwitansi(row, settings, petugas)
    path.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(path), pagesize=A4)
    W, H = A4
    ml, mr = 20 * mm, 20 * mm
    inner_w = W - ml - mr
    right_x = W - mr
    c.setLineWidth(1.4)
    c.setStrokeColor(GARIS)
    c.rect(10 * mm, 10 * mm, W - 20 * mm, H - 20 * mm)

    y = H - 22 * mm
    c.setFillColor(GARIS)
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
    c.line(ml, y, right_x, y)
    c.setLineWidth(0.6)
    c.line(ml, y - 1.4 * mm, right_x, y - 1.4 * mm)

    y -= 12 * mm
    c.setFillColor(colors.black)
    c.setFont("Helvetica-Bold", 10.5)
    c.drawCentredString(W / 2, y, "KWITANSI")
    c.setFont("Helvetica", 8)
    c.drawCentredString(W / 2, y - 4.6 * mm, "PEMBAYARAN PAJAK BUMI DAN BANGUNAN PERKOTAAN/PERDESAAN (PBB-P2)")
    y -= 13 * mm
    c.setFont("Helvetica", 9.5)
    c.drawString(ml, y, f"Nomor    : {d['nomor']}")
    c.drawRightString(right_x, y, f"Tanggal  : {d['tgl_cetak']}")

    y -= 5 * mm
    c.setFont("Helvetica", 8)
    c.setFillColor(ABU)
    c.drawString(ml, y, "Dibayar ke kas daerah melalui petugas pemungut PBB-P2 sesuai ketentuan yang berlaku.")
    c.setFillColor(colors.black)

    # ---- kotak data ----
    box_h = 76 * mm
    box_top = y - 5 * mm
    c.setStrokeColor(BOX)
    c.setLineWidth(0.9)
    c.rect(ml, box_top - box_h, inner_w * 0.60, box_h)
    x0 = ml + 6 * mm
    x1 = ml + inner_w * 0.60 - 6 * mm
    xsig0 = ml + inner_w * 0.64
    ys = box_top - 9 * mm

    def baris(label, nilai, yy, bold=False, size=9.5):
        c.setFont("Helvetica", 8.6)
        c.setFillColor(ABU)
        c.drawString(x0, yy, label)
        c.setFont("Helvetica-Bold" if bold else "Helvetica", size)
        c.setFillColor(colors.black)
        c.drawString(x0 + 26 * mm, yy, str(nilai))

    baris("Nama Penyetor", d["pembayar"], ys)
    ys -= 6.5 * mm
    baris("NOP", d["nop"], ys, bold=True, size=10.5)
    ys -= 6.5 * mm
    baris("Tahun Pajak", d["tahun"], ys)
    ys -= 6.5 * mm
    baris("Nama Wajib Pajak", d["nama_wp"], ys)
    ys -= 6.5 * mm
    c.setFont("Helvetica", 8.6)
    c.setFillColor(ABU)
    c.drawString(x0, ys, "Alamat Objek")
    c.setFillColor(colors.black)
    c.setFont("Helvetica", 9.2)
    teks = str(d["alamat"] or "-")
    from reportlab.lib.utils import simpleSplit
    for i, ln in enumerate(simpleSplit(teks, "Helvetica", 9.2, x1 - (x0 + 26 * mm))[:2]):
        c.drawString(x0 + 26 * mm, ys - i * 4 * mm, ln)
    ys -= 9 * mm
    baris("PBB Terutang", d["pokok"], ys)
    ys -= 6 * mm
    baris("Denda", d["denda"], ys)
    ys -= 6 * mm
    c.setFont("Helvetica-Bold", 10.5)
    c.drawString(x0, ys, "JUMLAH")
    c.drawString(x0 + 26 * mm, ys, f"{d['jumlah']}")
    c.setFont("Helvetica-Oblique", 8.6)
    c.drawString(x0 + 26 * mm, ys - 4.6 * mm, f"({d['terbilang']})")
    ys -= 10.5 * mm
    if d["billing"] and ys > box_top - box_h + 8 * mm:
        c.setFont("Helvetica", 8.6)
        c.setFillColor(ABU)
        c.drawString(x0, ys, "Kode Billing")
        c.setFillColor(colors.black)
        c.setFont("Helvetica-Bold", 9)
        c.drawString(x0 + 26 * mm, ys, d["billing"])

    # ---- tanda tangan ----
    box_bottom = box_top - box_h
    cx = xsig0 + (right_x - xsig0) / 2 + 2 * mm
    c.setFont("Helvetica", 9)
    c.setFillColor(colors.black)
    c.drawCentredString(cx, box_top - 9 * mm, "Menyetor,")
    c.drawCentredString(cx, box_top - 15.5 * mm, "Wajib Pajak / Kuasa")
    yy = box_top - 12 * mm
    if tanda_tangan_gambar and Path(tanda_tangan_gambar).exists():
        try:
            c.drawImage(tanda_tangan_gambar, right_x - 34 * mm, yy - 12 * mm, width=30 * mm,
                        height=13 * mm, mask="auto", preserveAspectRatio=True)
        except Exception:
            pass
    c.setFillColor(ABU)
    c.setFont("Helvetica", 7.6)
    c.drawCentredString(cx, yy - 13 * mm, "( ......................................... )")

    ytt = box_bottom + 26 * mm
    c.setFillColor(colors.black)
    c.setFont("Helvetica", 9)
    c.drawCentredString(cx, ytt + 8 * mm,
                        "Petugas Pemungut PBB-P2 " + (str(d["blok"]) if d["blok"] else "") + ",")
    if tanda_tangan_gambar and Path(tanda_tangan_gambar).exists():
        try:
            c.drawImage(tanda_tangan_gambar, right_x - 34 * mm, ytt - 9 * mm, width=30 * mm,
                        height=13 * mm, mask="auto", preserveAspectRatio=True)
        except Exception:
            pass
    c.setFont("Helvetica-Bold", 9.8)
    c.drawCentredString(cx, ytt - 14 * mm, d["petugas_nama"])
    if d["petugas_nip"]:
        c.setFont("Helvetica", 8)
        c.drawCentredString(cx, ytt - 18.5 * mm, "NIP. " + d["petugas_nip"])

    # Mengetahui (Lurah/Kepala Desa) — diletakkan di bawah kotak agar tidak menabrak
    if d["lurah_nama"]:
        ymk = box_bottom - 18 * mm
        c.setFont("Helvetica", 8.6)
        c.drawCentredString(cx, ymk, "Mengetahui, " + (settings.get("jabatan_lurah") or "Lurah / Kepala Desa"))
        c.setFont("Helvetica-Bold", 9.4)
        c.drawCentredString(cx, ymk - 24 * mm, d["lurah_nama"])
        c.setLineWidth(0.5)
        c.setStrokeColor(colors.HexColor("#9aa4b2"))
        c.line(cx - 25 * mm, ymk - 8 * mm, cx + 25 * mm, ymk - 8 * mm)
        if d["lurah_nip"]:
            c.setFont("Helvetica", 8)
            c.drawCentredString(cx, ymk - 28.5 * mm, "NIP. " + d["lurah_nip"])

    # ---- kaki ----
    baris_kaki = [f"Status setoran : {d['status']}",
                  f"Kode verifikasi : {d['cek_kode']}  |  Cek keaslian kwitansi di menu 'Cek Kwitansi' aplikasi loket "
                  f"atau lewat nomor kwitansi {d['nomor']}."]
    if d["catatan"]:
        baris_kaki.append("Catatan : " + d["catatan"])
    if d["no_rek"]:
        baris_kaki.append("Setor ke : " + d["no_rek"])
    tinggi_kaki = 6.2 * mm * len(baris_kaki) + 3 * mm
    fy = box_bottom - (52 * mm if d["lurah_nama"] else 10 * mm) - (10 * mm if len(baris_kaki) > 2 else 0)
    c.setStrokeColor(BOX)
    c.setLineWidth(0.6)
    c.rect(ml, fy - tinggi_kaki, inner_w, tinggi_kaki)
    c.setFont("Helvetica", 7.6)
    c.setFillColor(colors.black)
    for i, teks in enumerate(baris_kaki, start=1):
        c.drawString(ml + 4 * mm, fy - i * 6.2 * mm, teks[:150])
    c.setFont("Helvetica-Oblique", 7)
    c.drawRightString(right_x - 2 * mm, 13 * mm,
                      f"Dicetak {dt.datetime.now():%d/%m/%Y %H:%M} — Aplikasi Loket PBB")
    c.showPage()
    c.save()
    return path
