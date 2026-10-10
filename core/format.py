"""Utilitas format: rupiah, tanggal Indonesia, terbilang, dan normalisasi NOP."""
from __future__ import annotations

import datetime as dt
import re

BULAN = [
    "", "Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli",
    "Agustus", "September", "Oktober", "November", "Desember",
]
HARI = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]


def rp(n) -> str:
    """1234567 -> 'Rp1.234.567'"""
    try:
        n = int(round(float(n)))
    except (TypeError, ValueError):
        return "-"
    sign = "-" if n < 0 else ""
    s = f"{abs(n):,}".replace(",", ".")
    return f"{sign}Rp{s}"


def angka(n) -> str:
    try:
        return f"{int(round(float(n))):,}".replace(",", ".")
    except (TypeError, ValueError):
        return "0"


def tanggal(camel_or_iso, huruf: bool = False) -> str:
    """Terima 'YYYY-MM-DD', objek date/datetime, atau string Excel -> teks tanggal."""
    d = to_date(camel_or_iso)
    if not d:
        return "-"
    if huruf:
        return f"{d.day} {BULAN[d.month]} {d.year}"
    return f"{d.day:02d}/{d.month:02d}/{d.year}"


def to_date(value) -> dt.date | None:
    if value is None or value == "":
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        # serial Excel (epoch 1899-12-30)
        try:
            if 20000 < value < 80000:
                return (dt.date(1899, 12, 30) + dt.timedelta(days=int(value)))
        except Exception:
            return None
    s = str(value).strip()
    if not s or s in ("-", "0", "None", "NaN"):
        return None
    s = s.split(" ")[0].split("T")[0]
    pats = [
        "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%Y/%m/%d",
        "%d/%m/%y", "%d-%m-%y", "%d %b %Y", "%d %B %Y", "%y-%m-%d",
    ]
    for p in pats:
        try:
            return dt.datetime.strptime(s, p).date()
        except ValueError:
            continue
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", s)
    if m:
        y, mo, da = map(int, m.groups())
        try:
            return dt.date(y, mo, da)
        except ValueError:
            return None
    return None


_SATUAN = ["", "satu", "dua", "tiga", "empat", "lima", "enam", "tujuh", "delapan", "sembilan",
           "sepuluh", "sebelas"]


def _sebut(n: int) -> str:
    if n < 11:
        return _SATUAN[n]
    if n < 20:
        return f"{_sebut(n - 10)} belas"
    if n < 100:
        return f"{_sebut(n // 10)} puluh {_sebut(n % 10)}".replace("  ", " ").strip()
    if n < 200:
        return f"seratus {_sebut(n - 100)}".strip()
    if n < 1000:
        return f"{_sebut(n // 100)} ratus {_sebut(n % 100)}".replace("  ", " ").strip()
    if n < 2000:
        return f"seribu {_sebut(n - 1000)}".strip()
    if n < 1_000_000:
        return f"{_sebut(n // 1000)} ribu {_sebut(n % 1000)}".replace("  ", " ").strip()
    if n < 1_000_000_000:
        return f"{_sebut(n // 1_000_000)} juta {_sebut(n % 1_000_000)}".replace("  ", " ").strip()
    return f"{_sebut(n // 1_000_000_000)} miliar {_sebut(n % 1_000_000_000)}".replace("  ", " ").strip()


def terbilang(n) -> str:
    """285000 -> 'dua ratus delapan puluh lima ribu rupiah'"""
    try:
        n = int(round(float(n)))
    except (TypeError, ValueError):
        return "-"
    if n == 0:
        return "nol rupiah"
    kata = _sebut(abs(n)).strip()
    kata = " ".join(kata.split())
    return (f"minus {kata}" if n < 0 else kata) + " rupiah"


NOP_RE = re.compile(r"^\d{2}\.\d{2}\.\d{7}\.\d{4}\.\d{2}-\d$")
# bentuk warisan/pendek yang masih sering dipakai: prop.kab.kec+kel(6).urut(4)-blok(1)  = 17 digit
NOP_PENDOK = re.compile(r"^(\d{2})(\d{2})(\d{6})(\d{4})(\d)$")
MAX_LEN = (2, 2, 7, 4, 3)          # panjang tiap kelompok pada NOP 18 digit


def _dari_potongan(s_str: str) -> str:
    """Pakai struktur titik/garis untuk menata ulang NOP — kebal terhadap 0 depan yang hilang."""
    s = s_str.strip()
    tail = re.search(r"^(.*?)[- ](\d{1,2})$", s)
    blok_no = ""
    if tail and not tail.group(1).rstrip().isdigit():
        s, blok_no = tail.group(1).strip(), tail.group(2)
    potongan = [x for x in re.split(r"[.\-/ ]+", s) if x != ""]
    if not all(x.isdigit() for x in potongan) or len(potongan) < 4:
        return ""
    while len(potongan) < 5:
        potongan.append("")
    prop, kab, kec_ket, urut, sisa = potongan[:5]
    if blok_no:
        sisa = (sisa + blok_no)[-3:]
    sisa = sisa or "000"
    for nilai, batas in zip((prop, kab, kec_ket, urut, sisa), MAX_LEN):
        if len(nilai) > batas:
            return ""
    if len(prop) < 2 or len(kab) < 2:                       # jangan karang-karang propinsi/kabupaten
        return ""
    return (f"{prop.zfill(2)}{kab.zfill(2)}{kec_ket.zfill(7)}{urut.zfill(4)}"
            f"{sisa[0:2].zfill(2)}{(sisa[2] if len(sisa) > 2 else '0')}")


def norm_nop(value) -> str:
    """Normalisasi NOP apa pun menjadi 18 digit berformat '35.18.0100000.0001.02-0'.

    Didukung:
      • '35.18.0100000.0001.02-0'  (bentuk panjang, 18 digit)
      • '35.18.010001.0001-0'      (bentuk pendek warisan, 17 digit)
      • angka 18 digit polos hasil ketikan Excel (0 di depan boleh hilang -> dipakai struktur titik)
    Yang penting: file ketetapan & file realisasi dinormalisasi dengan fungsi yang sama, supaya
    NOP yang sama tidak pernah dihitung dua objek berbeda. Return '' bila benar-benar tidak terbaca.
    """
    if value is None:
        return ""
    s_str = str(value).strip()
    digits = re.sub(r"[^0-9]", "", s_str)
    if not digits:
        return ""
    if len(digits) == 18:
        d = digits
    else:
        rebuilt = _dari_potongan(s_str)
        if len(rebuilt) == 18:
            d = rebuilt
        elif len(digits) == 17 and NOP_PENDOK.fullmatch(digits):
            m = NOP_PENDOK.fullmatch(digits)
            prop, kab, kec_ket, urut, blok = m.groups()
            d = f"{prop}{kab}{kec_ket}0{urut.zfill(3)}{blok}"
        elif len(digits) == 16:                    # 0 depan terpotong Excel (2 digit paling depan)
            d = digits.zfill(18)
        else:
            return ""                              # tidak mungkin ditata tanpa titik pemisah
    if len(d) != 18 or not d.isdigit():
        return ""
    return f"{d[0:2]}.{d[2:4]}.{d[4:11]}.{d[11:15]}.{d[15:17]}-{d[17]}"


def nop_valid(value) -> bool:
    return bool(NOP_RE.match(norm_nop(value)))


def nop_polos(value) -> str:
    return re.sub(r"[^0-9]", "", norm_nop(value))


def slug(text: str) -> str:
    text = re.sub(r"[^A-Za-z0-9]+", "-", str(text or "")).strip("-").lower()
    return text or "tanpa-nama"


def persen(a: float, b: float) -> int:
    try:
        if not b:
            return 0
        return int(round(100.0 * float(a) / float(b)))
    except (TypeError, ValueError, ZeroDivisionError):
        return 0
