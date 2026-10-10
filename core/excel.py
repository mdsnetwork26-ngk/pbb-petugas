"""Impor/ekspor Excel. Toleran terhadap variasi nama kolom & letak baris header,
karena file ketetan dan file unduhan eSPPT sering berbeda format dari bulan ke bulan.
"""
from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from . import format as F

# ------------------------------------------------------------------- normalisasi


def _norm(text) -> str:
    s = str(text or "").upper()
    s = re.sub(r"[^A-Z0-9 ]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    # salah ketik yang sering muncul di file lapangan
    s = (s.replace("BANGUNAN", "BANGUNAN").replace("WAJIB PAJAK", "WP")
         .replace("PAJAK TERUTANG", "PBB")
         .replace("PPB", "PBB"))
    return s


_KETETAPAN_ALIASES: dict[str, list[str]] = {
    "nop": ["NOP", "N O P", "NO NOP", "NOP PBB", "NOMOR OBJEK PAJAK", "NO OBJEK PAJAK", "N O P PBB"],
    "tahun_pajak": ["TAHUN PAJAK", "TAHUN", "TP", "TAHUN PBB", "TAK TAHUN"],
    "nama_wp": ["NAMA WP", "NAMA WAJIB PAJAK", "NAMA", "NAMA WP PBB", "NAMAWP"],
    "alamat_objek": ["ALAMAT OBJEK PAJAK", "ALAMAT OBJEK", "ALAMAT OP", "ALAMAT"],
    "alamat_wp": ["ALAMAT WP", "ALAMAT WAJIB PAJAK"],
    "luas_bumi": ["LUAS BUMI", "LB", "LUAS TANAH", "LUAS BUMI M2", "LB M2", "LUAS"],
    "luas_bangunan": ["LUAS BANGUNAN", "LLB", "LUAS BANGUNAN M2", "LB BANGUNAN"],
    "pbb": ["PBB YG DIBAYAR", "PBB", "PBB TERUTANG", "JUMLAH PBB", "JUMLAH PAJAK", "JML PBB",
            "POKOK", "POKOK PBB", "TAGIHAN", "JUMLAH TAGIHAN", "PBB YG DIBAYAR (Rp)", "NILAI PBB"],
    "denda": ["DENDA", "BUNGA DENDA", "PAJAK TERUTANG"],   # hanya dipakai bila tidak ada 'pbb'
    "petugas": ["NAMA PETUGAS BLOK", "PETUGAS BLOK", "PETUGAS", "NAMA PETUGAS", "KOLOK", "BLOK",
                "KELOMPOK PETUGAS", "PEMUNGUT"],
    "kelurahan": ["KELURAHAN", "DESA", "KEL/DESA"],
    "kecamatan": ["KECAMATAN", "KEC"],
    "keterangan": ["KETERANGAN", "KET", "CATATAN"],
    "nomor_urut": ["NOMOR", "NO", "N O", "N0."],
}

_REALISASI_ALIASES: dict[str, list[str]] = {
    "nop": ["NOP", "N O P", "NOMOR OBJEK PAJAK"],
    "nama_wp": ["NAMA WP", "NAMA WAJIB PAJAK", "NAMA"],
    "alamat_wp": ["ALAMAT WP", "ALAMAT WAJIB PAJAK", "ALAMAT OBJEK PAJAK", "ALAMAT"],
    "tahun_pajak": ["TAHUN PAJAK", "TAHUN", "TP"],
    "pokok": ["POKOK", "POKOK PBB", "PBB"],
    "denda": ["DENDA", "BUNGA DENDA", "SANKSI"],
    "jumlah": ["JUMLAH PEMBAYARAN", "JUMLAH", "JML", "JUMLAH BAYAR", "NOMINAL", "TOTAL", "JUMLAH TAGIHAN"],
    "tgl_bayar": ["TANGGAL PEMBAYARAN", "TGL BAYAR", "TANGGAL BAYAR", "TGL BAYAR", "TANGGAL",
                  "TGL SETOR", "TANGGAL SETOR"],
    "channel": ["CHANNEL", "MEDIA", "TEMPAT BAYAR", "KANAL", "JALUR BAYAR", "CABANG", "ASAL",
                "KETERANGAN", "METODE"],
    "petugas": ["NAMA PETUGAS BLOK", "PETUGAS BLOK", "PETUGAS", "NAMA PETUGAS", "BLOK"],
}


def read_sheet(path: str | Path, kind: str) -> tuple[list[dict], str]:
    """Baca file xlsx/xlsm (atau csv) -> list baris yang sudah dipetakan.

    Return (rows, nama_sheet_yang_dipakai).
    """
    path = Path(path)
    aliases = _KETETAPAN_ALIASES if kind == "ketetapan" else _REALISASI_ALIASES
    if path.suffix.lower() == ".csv":
        return _read_csv(path, aliases, kind), "csv"

    wb = load_workbook(path, read_only=True, data_only=True)
    best_rows: list[dict] = []
    best_sheet = ""
    try:
        for ws in wb.worksheets:
            try:
                ws.reset_dimensions()      # max_row/max_column kadang tidak akurat di read_only
            except Exception:
                pass
            rows = []
            for r in ws.iter_rows(values_only=True):
                rows.append(list(r))
                if len(rows) >= 20000:     # pengaman file raksasa
                    break
            if not rows:
                continue
            mapping, header_idx = _find_mapping(rows, aliases)
            if not mapping:
                continue
            out = []
            for r in rows[header_idx + 1:]:
                d = _extract(r, mapping, kind)
                if d:
                    out.append(d)
            if len(out) > len(best_rows):
                best_rows, best_sheet = out, ws.title
    finally:
        wb.close()
    return best_rows, best_sheet


def _find_mapping(rows: list[list], aliases: dict[str, list[str]]) -> tuple[dict, int]:
    for idx, cells in enumerate(rows[:25]):
        scores: dict[str, int] = {}
        for key, alias_list in aliases.items():
            for a in alias_list:
                na = _norm(a)
                for ci, cell in enumerate(cells):
                    if _norm(cell) == na:
                        scores.setdefault(key, ci)
                        break
        if "nop" in scores and len(scores) >= 3:
            return scores, idx
    # baris terakhir percobaan: pakai header paling banyak terisi
    return {}, -1


def _num(value) -> int:
    if value is None or value == "":
        return 0
    if isinstance(value, (int, float)):
        return int(round(float(value)))
    s = str(value)
    neg = s.strip().startswith("-") or (s.count("(") and s.count(")"))
    s = re.sub(r"[^0-9]", "", s)
    try:
        n = int(s or 0)
    except ValueError:
        return 0
    return -n if neg else n


def _float(value) -> float:
    if value is None or value == "":
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    s = re.sub(r"[^0-9.,-]", "", str(value)).strip()
    if s.count(",") == 1 and s.count(".") == 0:
        s = s.replace(",", ".")
    else:
        s = s.replace(",", "")
    m = re.match(r"^-?\d+(\.\d+)?", s)
    return float(m.group(0)) if m else 0.0


def _txt(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return re.sub(r"\s+", " ", str(value)).strip()


def _extract(row: list, mapping: dict, kind: str) -> dict | None:
    def get(key):
        i = mapping.get(key)
        if i is None or i >= len(row):
            return None
        return row[i]

    mentah = _txt(get("nop"))
    if not any(v not in (None, "") for v in row):
        return None                                     # baris kosong
    nop = F.norm_nop(get("nop"))
    if not nop:
        # jangan buang diam-diam: diteruskan supaya pengimpor melaporkannya sebagai NOP tidak valid
        return {"nop": mentah, "tahun_pajak": _txt(get("tahun_pajak")),
                "nama_wp": _txt(get("nama_wp")), "petugas_blok": _txt(get("petugas")),
                "_nop_rusak": True}
    tahun = _txt(get("tahun_pajak"))
    if not tahun:
        tahun = str(dt.date.today().year)
    tahun = re.sub(r"[^0-9]", "", tahun)[:4] or str(dt.date.today().year)

    d = {"nop": nop, "tahun_pajak": tahun,
         "nama_wp": _txt(get("nama_wp")),
         "petugas_blok": _txt(get("petugas")) if kind == "ketetapan" else ""}

    if kind == "ketetapan":
        pbb = _num(get("pbb"))
        if pbb == 0:
            pbb = _num(get("denda"))
        d.update(
            alamat_objek=_txt(get("alamat_objek")) or _txt(get("alamat_wp")),
            alamat_wp=_txt(get("alamat_wp")),
            luas_bumi=_float(get("luas_bumi")),
            luas_bangunan=_float(get("luas_bangunan")),
            pbb=pbb,
            keterangan=_txt(get("keterangan")),
            kelurahan=_txt(get("kelurahan")),
            kecamatan=_txt(get("kecamatan")),
        )
    else:
        pokok = _num(get("pokok"))
        denda = _num(get("denda"))
        jumlah = _num(get("jumlah")) or (pokok + denda) or pokok
        d.update(
            alamat_wp=_txt(get("alamat_wp")),
            pokok=pokok, denda=denda, jumlah=abs(jumlah),
            tgl_bayar=_txt(get("tgl_bayar")),
            channel=_txt(get("channel")),
        )
    return d


def _read_csv(path: Path, aliases: dict, kind: str = "ketetapan") -> list[dict]:
    import csv

    raw = path.read_bytes()
    for enc in ("utf-8-sig", "latin-1", "cp1252"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        return []
    delim = ";" if text.count(";") > text.count(",") else ","
    rows = [line for line in csv.reader(text.splitlines(), delimiter=delim)]
    mapping, header_idx = _find_mapping(rows, aliases)
    if not mapping:
        return []
    out = []
    for r in rows[header_idx + 1:]:
        d = _extract(r, mapping, kind)
        if d:
            out.append(d)
    return out


# ------------------------------------------------------------------ laporan-out

THIN = Side(style="thin", color="9AA4B2")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
HEAD_FILL = PatternFill("solid", fgColor="1F4E79")
HEAD_FONT = Font(bold=True, color="FFFFFF", size=10)
TITLE_FONT = Font(bold=True, size=13, color="1F4E79")


def _style_header(ws, headers: list[str], title: str, subtitle: str = "") -> int:
    ws.cell(row=1, column=1, value=title).font = TITLE_FONT
    if subtitle:
        ws.cell(row=2, column=1, value=subtitle).font = Font(size=9, italic=True, color="555555")
    r = 4
    for c, h in enumerate(headers, 1):
        cell = ws.cell(row=r, column=c, value=h)
        cell.font = HEAD_FONT
        cell.fill = HEAD_FILL
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = BORDER
    ws.row_dimensions[r].height = 30
    ws.freeze_panes = ws.cell(row=r + 1, column=1)
    return r


def write_table(headers: list[str], rows: list[list], title: str, subtitle: str = "",
                money_cols: list[int] | None = None, widths: list[int] | None = None) -> Workbook:
    wb = Workbook()
    ws = wb.active
    ws.title = "Laporan"
    money_cols = money_cols or []
    hr = _style_header(ws, headers, title, subtitle)
    for i, row in enumerate(rows, start=hr + 1):
        for c, v in enumerate(row, 1):
            cell = ws.cell(row=i, column=c, value=v)
            cell.border = BORDER
            cell.font = Font(size=10)
            if c in money_cols:
                cell.number_format = '#,##0'
                cell.alignment = Alignment(horizontal="right")
            elif isinstance(v, str) and len(v) > 40:
                cell.alignment = Alignment(wrap_text=True, vertical="top")
            else:
                cell.alignment = Alignment(vertical="top")
    for c in range(1, len(headers) + 1):
        w = (widths[c - 1] if widths and c - 1 < len(widths) else 16)
        ws.column_dimensions[get_column_letter(c)].width = w
    return wb


def save(wb: Workbook, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path
