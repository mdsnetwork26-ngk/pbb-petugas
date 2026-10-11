"""Logika bisnis: impor ketetapan, catat pembayaran, verifikasi realisasi, laporan per blok,
dan orkestrasi billing/sinkron eSPPT (jalan di background thread)."""
from __future__ import annotations

import datetime as dt
import re
import threading
import time
import traceback

from . import format as F
from .db import DATA_AUTO_DIR, connect, get_settings, json_dump, now, setting_bool

STATUS_BAYAR = {
    "belum_bayar": "Belum Bayar",
    "proses": "Dalam Proses",
    "lunas": "Lunas",
}

# state sinkronisasi (untuk polling dari UI)
SINKRON = {
    "berjalan": False, "selesai": None, "total": 0, "sudah": 0,
    "log": [], "hasil": {}, "mode": "",
    "lock": threading.Lock(),
}


def _sink_push(msg: str, level: str = "info") -> None:
    with SINKRON["lock"]:
        SINKRON["log"].append({"t": dt.datetime.now().strftime("%H:%M:%S"),
                               "level": level, "msg": msg})
        SINKRON["log"] = SINKRON["log"][-250:]


def sinkron_state() -> dict:
    with SINKRON["lock"]:
        return {
            "berjalan": SINKRON["berjalan"], "selesai": SINKRON["selesai"],
            "total": SINKRON["total"], "sudah": SINKRON["sudah"], "log": list(SINKRON["log"]),
            "hasil": dict(SINKRON["hasil"]), "mode": SINKRON["mode"],
        }


# --------------------------------------------------------------- tahun & filter

def daftar_tahun(conn) -> list[str]:
    rows = conn.execute("SELECT DISTINCT tahun_pajak FROM tagihan ORDER BY tahun_pajak DESC").fetchall()
    out = [r["tahun_pajak"] for r in rows]
    this = str(dt.date.today().year)
    if this not in out:
        out.insert(0, this)
    return out


def tagihan_view_sql(where: str = "", order: str = "t.nop"):
    return f"""
    SELECT t.*, p.nama AS petugas_nama, p.blok AS petugas_blok,
      (SELECT COALESCE(SUM(b.jumlah_diterima),0) FROM pembayaran b
        WHERE b.tagihan_id = t.id AND b.void = 0) AS dibayar,
      (SELECT b.id FROM pembayaran b WHERE b.tagihan_id = t.id AND b.void = 0
        ORDER BY b.id DESC LIMIT 1) AS pembayaran_id,
      (SELECT b.status FROM pembayaran b WHERE b.tagihan_id = t.id AND b.void = 0
        ORDER BY b.id DESC LIMIT 1) AS bayar_status,
      (SELECT b.nomor_kwitansi FROM pembayaran b WHERE b.tagihan_id = t.id AND b.void = 0
        ORDER BY b.id DESC LIMIT 1) AS nomor_kwitansi,
      (SELECT b.tgl_bayar FROM pembayaran b WHERE b.tagihan_id = t.id AND b.void = 0
        ORDER BY b.id DESC LIMIT 1) AS tgl_bayar,
      (SELECT CASE WHEN b.id IS NULL THEN 0 WHEN b.status = 'terverifikasi' THEN 2 ELSE 1 END
        FROM pembayaran b WHERE b.tagihan_id = t.id AND b.void = 0
        ORDER BY b.id DESC LIMIT 1) AS st_urut
    FROM tagihan t LEFT JOIN petugas p ON p.id = t.petugas_id
    {where}
    ORDER BY {order}
    """


def status_tagihan(row) -> str:
    """Diturunkan dari pembayaran terakhir — tidak disimpan, jadi tidak bisa basi."""
    if not row["pembayaran_id"]:
        return "belum_bayar"
    st = row["bayar_status"]
    return "lunas" if st == "terverifikasi" else "proses"


# ------------------------------------------------------------------ petugas

def daftar_petugas(conn, aktif_saja: bool = False) -> list:
    q = "SELECT * FROM petugas" + (" WHERE aktif = 1" if aktif_saja else "") + " ORDER BY blok"
    return [dict(r) for r in conn.execute(q)]


def save_petugas(conn, data: dict) -> int:
    if data.get("id"):
        conn.execute(
            "UPDATE petugas SET nama=?, blok=?, wilayah=?, telepon=?, nip=?, jabatan=?, aktif=? WHERE id=?",
            (data["nama"], data["blok"], data.get("wilayah", ""), data.get("telepon", ""),
             data.get("nip", ""), data.get("jabatan", "Petugas Pemungut PBB-P2"),
             1 if data.get("aktif") else 0, data["id"]))
        return int(data["id"])
    cur = conn.execute(
        "INSERT INTO petugas(nama,blok,wilayah,telepon,nip,jabatan,aktif) VALUES(?,?,?,?,?,?,?)",
        (data["nama"], data["blok"], data.get("wilayah", ""), data.get("telepon", ""),
         data.get("nip", ""), data.get("jabatan", "Petugas Pemungut PBB-P2"),
         1 if data.get("aktif", True) else 0))
    return int(cur.lastrowid)


def resolve_petugas(conn, nama_blok: str, cache: dict | None = None) -> int | None:
    """Cari id petugas berdasar nama blok / 'Blok 1' / nama orang. Buat otomatis bila baru."""
    key = (nama_blok or "").strip()
    if not key:
        return None
    if cache is not None and key in cache:
        return cache[key]
    norm = re.sub(r"[^a-z0-9]", "", key.lower())
    for r in conn.execute("SELECT id, nama, blok FROM petugas"):
        for cand in (r["blok"], r["nama"]):
            if re.sub(r"[^a-z0-9]", "", (cand or "").lower()) == norm and norm:
                if cache is not None:
                    cache[key] = r["id"]
                return r["id"]
    # cocok longgar: angka blok saja ("1" / "BLOK 1" / "3 (tiga)")
    digits = re.sub(r"\D", "", key)
    if digits:
        for r in conn.execute("SELECT id, blok FROM petugas"):
            if re.sub(r"\D", "", r["blok"] or "") == digits:
                if cache is not None:
                    cache[key] = r["id"]
                return r["id"]
    blok = key if len(key) <= 24 else key[:24]
    cur = conn.execute("INSERT INTO petugas(nama,blok,aktif) VALUES(?,?,1)",
                       (f"Petugas {blok}", blok))
    if cache is not None:
        cache[key] = int(cur.lastrowid)
    return int(cur.lastrowid)


# --------------------------------------------------------- denda jatuh tempo

def hitung_denda(pokok, tahun_pajak, s: dict, hari=None) -> int:
    """Denda administratif = persen × bulan terlampaui setelah bulan jatuh tempo.

    Jatuh tempo dihitung per tahun pajak: bulan 'jatuh_tempo_bulan' tahun itu.
    Contoh (setting bawaan): jatuh tempo Agustus -> SPPT 2026 mulai kena denda
    pada September 2026 (1 bulan), Oktober 2026 (2 bulan), dst., maks 24 bulan.
    """
    pokok = int(pokok or 0)
    if pokok <= 0:
        return 0
    jt = re.sub(r"\D", "", str(s.get("jatuh_tempo_bulan", "8") or "8"))
    jt = min(max(int(jt) if jt else 8, 1), 12)
    try:
        persen = float(str(s.get("denda_persen", "1") or 0).replace(",", "."))
    except ValueError:
        persen = 1.0
    if persen <= 0:
        return 0
    maks = re.sub(r"\D", "", str(s.get("denda_maks_bulan", "24") or "24"))
    maks = max(int(maks) if maks else 24, 0)
    try:
        tahun = int(str(tahun_pajak)[:4])
    except (TypeError, ValueError):
        return 0
    hari = hari or dt.date.today()
    bulan = (hari.year - tahun) * 12 + (hari.month - jt)
    if bulan <= 0:
        return 0
    return int(round(pokok * persen / 100.0 * min(bulan, maks)))


def perbarui_denda(conn, hari=None) -> int:
    """Hitung ulang denda tagihan aktif yang belum tercatat pembayarannya.

    Tagihan yang sudah memiliki pembayaran aktif TIDAK disentuh — denda dibekukan
    pada angka saat kwitansi terbit, sesuai yang dijanjikan ke wajib pajak.
    """
    s = get_settings(conn)
    n = 0
    for r in conn.execute(
            """SELECT t.id, t.pokok, t.tahun_pajak, t.denda FROM tagihan t
               WHERE t.aktif = 1 AND NOT EXISTS
                 (SELECT 1 FROM pembayaran b WHERE b.tagihan_id = t.id AND b.void = 0)"""):
        baru = hitung_denda(r["pokok"], r["tahun_pajak"], s, hari)
        if baru != (r["denda"] or 0):
            conn.execute("UPDATE tagihan SET denda=?, updated_at=? WHERE id=?",
                         (baru, now(), r["id"]))
            n += 1
    if n:
        conn.commit()
    return n


# ------------------------------------------------------- usulan pindah blok WP

def daftar_usulan(conn, status: str = "", petugas_lama: int | None = None) -> list[dict]:
    where, args = [], []
    if status:
        where.append("u.status = ?")
        args.append(status)
    if petugas_lama:
        where.append("u.petugas_lama = ?")
        args.append(petugas_lama)
    sql = """SELECT u.*, t.nop, t.nama_wp, t.tahun_pajak, t.pokok, t.denda,
                    pl.blok AS blok_lama, pl.nama AS nama_lama,
                    pb.blok AS blok_baru, pb.nama AS nama_baru
             FROM usulan_pindah u
             JOIN tagihan t ON t.id = u.tagihan_id
             LEFT JOIN petugas pl ON pl.id = u.petugas_lama
             LEFT JOIN petugas pb ON pb.id = u.petugas_baru"""
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY (u.status = 'menunggu') DESC, u.id DESC"
    return [dict(r) for r in conn.execute(sql, args)]


def ajukan_pindah(conn, tagihan_id: int, petugas_baru: int, alasan: str, user_nama: str) -> int:
    tag = conn.execute("SELECT * FROM tagihan WHERE id=? AND aktif=1", (tagihan_id,)).fetchone()
    if not tag:
        raise ValueError("Data ketetapan (NOP) tidak ditemukan atau tidak aktif.")
    if not petugas_baru:
        raise ValueError("Blok tujuan wajib dipilih.")
    if petugas_baru == tag["petugas_id"]:
        raise ValueError("Blok tujuan sama dengan blok yang memegang NOP sekarang.")
    pending = conn.execute("SELECT id FROM usulan_pindah WHERE tagihan_id=? AND status='menunggu'",
                           (tagihan_id,)).fetchone()
    if pending:
        raise ValueError("NOP ini sudah ada usulan pindahnya dan sedang menunggu keputusan admin.")
    cur = conn.execute(
        """INSERT INTO usulan_pindah(tagihan_id, petugas_lama, petugas_baru, alasan,
             status, diajukan_oleh, diajukan_pada)
           VALUES(?,?,?,?, 'menunggu', ?, ?)""",
        (tagihan_id, tag["petugas_id"], petugas_baru, alasan, user_nama, now()))
    return int(cur.lastrowid)


def putuskan_pindah(conn, usulan_id: int, setuju: bool, catatan: str = "") -> str:
    u = conn.execute("SELECT * FROM usulan_pindah WHERE id=?", (usulan_id,)).fetchone()
    if not u:
        raise ValueError("Usulan tidak ditemukan.")
    if u["status"] != "menunggu":
        raise ValueError("Usulan ini sudah diputuskan sebelumnya.")
    if setuju:
        conn.execute("UPDATE tagihan SET petugas_id=?, updated_at=? WHERE id=?",
                     (u["petugas_baru"], now(), u["tagihan_id"]))
        st = "disetujui"
    else:
        st = "ditolak"
    conn.execute("UPDATE usulan_pindah SET status=?, catatan_admin=?, diputuskan_pada=? WHERE id=?",
                 (st, catatan, now(), usulan_id))
    return st


def hapus_petugas(conn, petugas_id: int, pindah_ke: int | None = None) -> dict:
    """Hapus petugas blok.

    - NOP yang dipegang dipindahkan ke petugas pengganti (pindah_ke) atau dilepas (None).
    - Usulan pindah yang sedang menunggu dan menunjuk blok ini ditolak otomatis.
    - Akun login yang terikat petugas ini dinonaktifkan (riwayat kwitansi tidak diubah).
    """
    p = conn.execute("SELECT * FROM petugas WHERE id=?", (petugas_id,)).fetchone()
    if not p:
        raise ValueError("Petugas tidak ditemukan.")
    tujuan = None
    if pindah_ke is not None:
        tujuan = conn.execute("SELECT * FROM petugas WHERE id=?", (pindah_ke,)).fetchone()
        if not tujuan or tujuan["id"] == petugas_id:
            raise ValueError("Petugas pengganti tidak valid.")
    n_nop = conn.execute("SELECT COUNT(*) c FROM tagihan WHERE petugas_id=?",
                         (petugas_id,)).fetchone()["c"]
    if n_nop:
        conn.execute("UPDATE tagihan SET petugas_id=?, updated_at=? WHERE petugas_id=?",
                     (pindah_ke, now(), petugas_id))
    n_usulan = conn.execute(
        "UPDATE usulan_pindah SET status='ditolak', "
        "catatan_admin=TRIM(catatan_admin || ' | Petugas blok tujuan dihapus'), diputuskan_pada=? "
        "WHERE petugas_baru=? AND status='menunggu'", (now(), petugas_id)).rowcount
    users = [r["username"] for r in conn.execute(
        "SELECT username FROM users WHERE petugas_id=?", (petugas_id,))]
    conn.execute("UPDATE users SET aktif=0 WHERE petugas_id=?", (petugas_id,))
    conn.execute("DELETE FROM petugas WHERE id=?", (petugas_id,))
    return {"nama": p["nama"], "blok": p["blok"], "n_nop": n_nop,
            "tujuan": f"{tujuan['blok']} — {tujuan['nama']}" if tujuan else None,
            "n_usulan_ditolak": n_usulan or 0, "users": users}


# ------------------------------------------------------------ impor ketetapan

def impor_ketetapan(conn, rows: list[dict], tahun_default: str, file_name: str,
                    user: str, aktifkan_semua: bool = False) -> dict:
    """Upsert per (tahun_pajak, nop). Data pembayaran lama tidak pernah dihapus."""
    cache: dict = {}
    stat = {"baru": 0, "ubah": 0, "sama": 0, "tanpa_nominal": 0, "nop_salah": 0, "nonaktif": 0}
    ubah = []
    tahun_yg_ada = set()
    touched = set()
    for i, d in enumerate(rows, start=2):
        if not d:
            stat["nop_salah"] += 1
            continue
        nop = F.norm_nop(d.get("nop"))
        if not nop:
            stat["nop_salah"] += 1
            continue
        tahun = (d.get("tahun_pajak") or tahun_default or str(dt.date.today().year))
        tahun = re.sub(r"\D", "", tahun)[:4] or str(dt.date.today().year)
        tahun_yg_ada.add(tahun)
        pid = resolve_petugas(conn, d.get("petugas_blok", ""), cache)
        pbb = int(d.get("pbb") or 0)
        if pbb <= 0:
            stat["tanpa_nominal"] += 1
        old = conn.execute("SELECT * FROM tagihan WHERE tahun_pajak=? AND nop=?",
                           (tahun, nop)).fetchone()
        if old is None:
            conn.execute(
                """INSERT INTO tagihan(tahun_pajak,nop,nama_wp,alamat_objek,alamat_wp,luas_bumi,
                     luas_bangunan,pokok,denda,petugas_id,kelurahan,kecamatan,keterangan,aktif,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (tahun, nop, d.get("nama_wp", ""), d.get("alamat_objek", ""), d.get("alamat_wp", ""),
                 float(d.get("luas_bumi") or 0), float(d.get("luas_bangunan") or 0), pbb,
                 int(d.get("denda") or 0), pid, d.get("kelurahan", ""), d.get("kecamatan", ""),
                 d.get("keterangan", ""), 1, now(), now()))
            stat["baru"] += 1
        else:
            berubah = (
                (old["nama_wp"] or "") != d.get("nama_wp", "")
                or (old["alamat_objek"] or "") != d.get("alamat_objek", "")
                or old["pokok"] != pbb
                or int(old["petugas_id"] or 0) != int(pid or 0)
                or float(old["luas_bumi"] or 0) != float(d.get("luas_bumi") or 0)
                or float(old["luas_bangunan"] or 0) != float(d.get("luas_bangunan") or 0)
            )
            conn.execute(
                """UPDATE tagihan SET nama_wp=?, alamat_objek=?, alamat_wp=?, luas_bumi=?, luas_bangunan=?,
                       pokok=?, petugas_id=?, kelurahan=?, kecamatan=?, keterangan=?,
                       aktif=CASE WHEN ? THEN 1 ELSE aktif END, updated_at=? WHERE id=?""",
                (d.get("nama_wp", "") or old["nama_wp"], d.get("alamat_objek", "") or old["alamat_objek"],
                 d.get("alamat_wp", "") or old["alamat_wp"], float(d.get("luas_bumi") or 0),
                 float(d.get("luas_bangunan") or 0), pbb, pid, d.get("kelurahan", "") or old["kelurahan"],
                 d.get("kecamatan", "") or old["kecamatan"], d.get("keterangan", ""),
                 1 if aktifkan_semua else 0, now(), old["id"]))
            stat["ubah" if berubah else "sama"] += 1
            if berubah:
                ubah.append({"nop": nop, "tahun": tahun,
                             "lama": f"{old['nama_wp']} / {F.rp(old['pokok'])}",
                             "baru": f"{d.get('nama_wp')} / {F.rp(pbb)}"})
        touched.add((tahun, nop))
    # NOP yang tidak lagi muncul di file tahun berjalan -> nonaktif (tidak dihapus)
    if aktifkan_semua:
        for r in conn.execute("SELECT id, tahun_pajak, nop FROM tagihan WHERE aktif = 1"):
            if (r["tahun_pajak"], r["nop"]) not in touched and str(r["tahun_pajak"]) in tahun_yg_ada:
                conn.execute("UPDATE tagihan SET aktif = 0, updated_at = ? WHERE id = ?", (now(), r["id"]))
                stat["nonaktif"] += 1
    if stat["nop_salah"]:
        stat["pesan"] = (f"{stat['nop_salah']} baris dilewati karena NOP tidak terbaca. NOP harus 18 digit "
                         "dengan titik pemisah, mis. 35.18.0100000.0001.02-0 atau bentuk pendek "
                         "35.18.010001.0001-0. Kalau file Excel menyimpan NOP sebagai angka, format "
                         "kolomnya ubah ke Teks agar angka 0 di depan tidak hilang.")
    else:
        stat["pesan"] = ""
    stat["tahun"] = sorted(tahun_yg_ada)
    stat["ubah_detail"] = ubah[:40]
    stat["total_baris"] = len(rows)
    cur = conn.execute(
        "INSERT INTO import_batch(jenis,nama_file,tahun_pajak,info,stat,created_at,created_by) "
        "VALUES(?,?,?,?,?,?,?)",
        ("ketetapan", file_name, ",".join(stat["tahun"]), f"{len(rows)} baris dibaca",
         json_dump({k: v for k, v in stat.items() if k != "ubah_detail"}), now(), user))
    stat["batch_id"] = cur.lastrowid
    return stat


def impor_realisasi(conn, rows: list[dict], file_name: str, user: str,
                    auto_buat: bool | None = None, tahun_default: str = "") -> dict:
    """Simpan data realisasi lalu cocokkan ke pembayaran. Bekerja untuk file apa pun
    yang punya kolom NOP + tahun + jumlah/tanggal bayar (termasuk unduhan eSPPT)."""
    s = get_settings(conn)
    auto_buat = s.get("auto_lunas_realisasi", "1") in ("1", "true", "ya") if auto_buat is None else auto_buat
    tahun_default = tahun_default or str(dt.date.today().year)
    stat = {"baris": 0, "skip": 0, "verifikasi": 0, "bayar_sendiri": 0, "beda": 0, "tidak_ada_di_ketetapan": 0}
    tahun_set: list[str] = []
    for d in rows:
        nop = F.norm_nop(d.get("nop"))
        if not nop:
            stat["skip"] += 1
            continue
        tahun = re.sub(r"\D", "", str(d.get("tahun_pajak") or ""))[:4] or str(dt.date.today().year)
        tgl = F.tanggal(F.to_date(d.get("tgl_bayar")) or dt.date.today())
        tgl_iso = (F.to_date(d.get("tgl_bayar")) or dt.date.today()).isoformat()
        jumlah = int(d.get("jumlah") or 0)
        pokok = int(d.get("pokok") or 0) or jumlah
        denda = int(d.get("denda") or 0)
        cur = conn.execute(
            """INSERT OR IGNORE INTO realisasi(nop,nama_wp,alamat_wp,tahun_pajak,pokok,denda,jumlah,
                 tgl_bayar,channel,batch_id,created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (nop, d.get("nama_wp", ""), d.get("alamat_wp", ""), tahun, pokok, denda, jumlah,
             tgl_iso, d.get("channel", ""), None, now()))
        stat["baris"] += 1
        if tahun not in tahun_set:
            tahun_set.append(tahun)
        tag = conn.execute("SELECT * FROM tagihan WHERE tahun_pajak=? AND nop=?", (tahun, nop)).fetchone()
        if not tag:
            stat["tidak_ada_di_ketetapan"] += 1
            continue
        byr = conn.execute(
            "SELECT * FROM pembayaran WHERE tagihan_id=? AND void=0 ORDER BY id DESC LIMIT 1",
            (tag["id"],)).fetchone()
        if byr:
            conn.execute("UPDATE realisasi SET matched_pembayaran_id=? WHERE id=?",
                         (byr["id"], cur.lastrowid or byr["id"]))
            if byr["status"] != "terverifikasi":
                conn.execute(
                    "UPDATE pembayaran SET status='terverifikasi', tgl_verifikasi=?, updated_at=?, "
                    "catatan = CASE WHEN catatan LIKE '%Realisasi eSPPT%' THEN catatan "
                    "ELSE TRIM(catatan || ' | Terverifikasi realisasi eSPPT ' || ?) END WHERE id=?",
                    (tgl_iso, now(), tgl, byr["id"]))
                stat["verifikasi"] += 1
            if jumlah and byr["jumlah_diterima"] and abs(jumlah - byr["jumlah_diterima"]) > 1000:
                stat["beda"] += 1
        elif auto_buat:
            conn.execute(
                """INSERT INTO pembayaran(tagihan_id,tgl_bayar,metode,jumlah_diterima,billing_code,
                     status,pembayar,catatan,tgl_verifikasi,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (tag["id"], tgl_iso, "bank", jumlah or pokok, "", "terverifikasi",
                 d.get("nama_wp", ""), "Auto-import dari data realisasi eSPPT (bayar sendiri ke bank/pos)",
                 tgl_iso, now(), now()))
            stat["bayar_sendiri"] += 1
    stat["total_baris"] = len(rows)
    conn.execute(
        "INSERT INTO import_batch(jenis,nama_file,tahun_pajak,info,stat,created_at,created_by) "
        "VALUES(?,?,?,?,?,?,?)",
        ("realisasi", file_name, ",".join(tahun_set) or tahun_default, f"{len(rows)} baris",
         json_dump({k: v for k, v in stat.items()}), now(), user))
    return stat


# ------------------------------------------------------------------ pembayaran

def nomor_kwitansi_baru(conn, tahun: str, petugas_row: dict | None = None) -> str:
    """Nomor kwitansi: MM/TAHUN/NNN (urut per bulan per tahun pajak, tidak didaur ulang saat batal)."""
    prefix = f"{dt.date.today():%m}/{tahun}"
    row = conn.execute(
        "SELECT nomor_kwitansi FROM pembayaran WHERE nomor_kwitansi LIKE ? "
        "ORDER BY LENGTH(nomor_kwitansi) DESC, nomor_kwitansi DESC LIMIT 1", (f"{prefix}/%",)).fetchone()
    try:
        terakhir = int(str(row["nomor_kwitansi"]).rsplit("/", 1)[1]) if row else 0
    except ValueError:
        terakhir = 0
    for i in range(1, 60):
        nomor = f"{prefix}/{terakhir + i:03d}"
        if not conn.execute("SELECT 1 FROM pembayaran WHERE nomor_kwitansi=?", (nomor,)).fetchone():
            return nomor
    return f"{prefix}/{dt.datetime.now():%H%M%S}"


def catat_pembayaran(conn, tagihan_id: int, data: dict, petugas_id: int | None,
                     user_role: str = "admin") -> dict:
    tag = conn.execute("SELECT * FROM tagihan WHERE id=?", (tagihan_id,)).fetchone()
    if not tag:
        raise ValueError("Data ketetapan (NOP) tidak ditemukan.")
    s = get_settings(conn)
    lama = conn.execute(
        "SELECT id FROM pembayaran WHERE tagihan_id=? AND void=0 ORDER BY id DESC LIMIT 1",
        (tagihan_id,)).fetchone()
    if lama:
        raise ValueError("NOP ini sudah tercatat lunas/diproses. Batalkan dulu data lama bila ingin mengoreksi.")
    petugas_id = petugas_id or tag["petugas_id"]      # default: petugas yang memegang NOP tsb
    jumlah = int(data.get("jumlah") or tag["pokok"] + tag["denda"])
    if jumlah < 0:
        raise ValueError("Nominal tidak boleh negatif.")
    metode = data.get("metode") or "petugas"
    status = "belum_tagihan" if metode != "petugas" else (
        "terkirim" if setting_bool(s, "skip_billing") else "menunggu_billing")
    if metode != "petugas":
        status = "terverifikasi" if setting_bool(s, "auto_lunas_bayar_sendiri") else "belum_tagihan"
    nomor = nomor_kwitansi_baru(conn, tag["tahun_pajak"]) if metode == "petugas" else None
    tgl = data.get("tgl_bayar") or dt.date.today().isoformat()
    cur = conn.execute(
        """INSERT INTO pembayaran(tagihan_id,nomor_kwitansi,tgl_bayar,metode,jumlah_diterima,
             pembayar,status,tgl_billing,catatan,petugas_id,created_at,updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
        (tagihan_id, nomor, tgl, metode, jumlah, data.get("pembayar") or tag["nama_wp"], status,
         tgl if status == "terkirim" else None, data.get("catatan", ""), petugas_id, now(), now()))
    conn.execute("UPDATE tagihan SET updated_at=? WHERE id=?", (now(), tagihan_id))
    return {"id": cur.lastrowid, "nomor_kwitansi": nomor, "status": status, "tgl_bayar": tgl,
            "jumlah": jumlah, "metode": metode, "terbilang": F.terbilang(jumlah)}


def batalkan_pembayaran(conn, pembayaran_id: int) -> None:
    conn.execute("UPDATE pembayaran SET void=1, updated_at=?, "
                 "catatan = TRIM(catatan || ' | Dibatalkan ' || ?) WHERE id=?",
                 (now(), f"{dt.date.today():%d/%m/%Y}", pembayaran_id))


def daftar_antrean_billing(conn, tahun: str = "", petugas_id: int | None = None) -> list:
    where = ["b.void = 0", "b.status IN ('menunggu_billing','belum_tagihan')",
             "COALESCE(b.billing_code,'') = ''"]
    args: list = []
    if tahun:
        where.append("t.tahun_pajak = ?")
        args.append(tahun)
    if petugas_id:
        where.append("b.petugas_id = ?")
        args.append(petugas_id)
    sql = f"""
      SELECT b.id, b.jumlah_diterima, b.tgl_bayar, b.nomor_kwitansi, t.nop, t.tahun_pajak,
             t.nama_wp, t.pokok, t.denda, p.blok, p.nama AS petugas_nama
      FROM pembayaran b JOIN tagihan t ON t.id = b.tagihan_id
      LEFT JOIN petugas p ON p.id = b.petugas_id
      WHERE {' AND '.join(where)}
      ORDER BY b.tgl_bayar, t.nop"""
    return [dict(r) for r in conn.execute(sql, args)]


def _log_api(conn, pembayaran_id, aksi, url, req, status, resp, ok):
    conn.execute(
        "INSERT INTO billing_log(pembayaran_id,aksi,url,req,http_status,resp,ok,created_at) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (pembayaran_id, aksi, url, (req or "")[:900], status, (resp or "")[:900],
         1 if ok else 0, now()))


def jalankan_billing(conn, ids: list[int], user: str = "-") -> dict:
    from .esppt import EspptClient

    s = get_settings(conn)
    client = EspptClient(s, log=lambda *a: None)
    res = {"sukses": 0, "gagal": 0, "detail": []}
    if not client.enabled:
        raise RuntimeError("Otomatisasi eSPPT masih MATI. Aktifkan di menu Pengaturan.")
    for pid in ids:
        row = conn.execute(
            """SELECT b.*, t.nop, t.tahun_pajak, t.pokok, t.denda FROM pembayaran b
               JOIN tagihan t ON t.id = b.tagihan_id WHERE b.id=?""", (pid,)).fetchone()
        if not row:
            continue
        if not client.logged_in:
            lr = client.login()
            if not lr.ok:
                _log_api(conn, pid, "login", lr.url, "", lr.status, lr.error, False)
                res["gagal"] += 1
                res["detail"].append({"id": pid, "nop": row["nop"], "ok": False,
                                      "pesan": "Login eSPPT gagal: " + (lr.error or "?")})
                continue
        r = client.buat_billing(row["nop"], row["tahun_pajak"], row["denda"])
        _log_api(conn, pid, "billing", r.url, "", r.status, r.text or r.error, r.ok)
        if r.ok:
            conn.execute("UPDATE pembayaran SET status='terkirim', billing_code=?, "
                         "tgl_billing=?, updated_at=? WHERE id=?",
                        (r.text, dt.date.today().isoformat(), now(), pid))
            res["sukses"] += 1
            res["detail"].append({"id": pid, "nop": row["nop"], "ok": True, "pesan": "Kode billing " + r.text})
        else:
            res["gagal"] += 1
            res["detail"].append({"id": pid, "nop": row["nop"], "ok": False,
                                  "pesan": (r.error or "gagal tanpa pesan")})
    return res


# ------------------------------------------------ folder "data/auto" (jemput file)

def proses_folder_auto(conn, user: str = "jadwal") -> list[dict]:
    """Ambil file Excel/CSV yang ditaruh di data/auto/ lalu cocokkan (tanpa perlu membuka browser).

    Nama file berisi 'ketetapan'/'tagihan' -> dianggap data ketetapan; selain itu dianggap realisasi.
    File dipindah ke data/auto/sudah-diproses/ supaya tidak terhitung dua kali.
    """
    from .excel import read_sheet

    folder = DATA_AUTO_DIR
    if not folder.exists():
        return []
    Arsip = folder / "sudah-diproses"
    Arsip.mkdir(exist_ok=True)
    hasil = []
    for path in sorted(folder.glob("*")):
        if path.is_dir() or path.suffix.lower() not in (".xlsx", ".xlsm", ".xls", ".csv"):
            continue
        jenis = "ketetapan" if re.search(r"ketetap|tagihan|sppt", path.name, re.I) else "realisasi"
        try:
            rows, sheet = read_sheet(path, jenis)
            if not rows:
                raise ValueError("tidak ada baris data yang terbaca")
            tahun = str(dt.date.today().year)
            if jenis == "ketetapan":
                stat = impor_ketetapan(conn, rows, tahun, path.name, user, False)
            else:
                stat = impor_realisasi(conn, rows, path.name, user)
            conn.commit()
            stat["file"] = path.name
            stat["jenis"] = jenis
            stat["ok"] = True
            path.replace(Arsip / f"{dt.datetime.now():%Y%m%d-%H%M%S}-{path.name}")
        except Exception as e:
            stat = {"file": path.name, "jenis": jenis, "ok": False, "error": f"{type(e).__name__}: {e}"}
            try:
                path.replace(Arsip / f"GAGAL-{path.name}")
            except OSError:
                pass
        hasil.append(stat)
    return hasil


# ------------------------------------------------------ laporan progres per blok

def laporan_blok(conn, tahun: str, bulan: str = "", tgl_dari: str = "", tgl_sampai: str = "") -> list[dict]:
    where = ["t.aktif = 1"]
    args: list = []
    if tahun:
        where.append("t.tahun_pajak = ?")
        args.append(tahun)
    if tgl_dari:
        where.append("DATE(b.tgl_bayar) >= ?")
        args.append(tgl_dari)
    if tgl_sampai:
        where.append("DATE(b.tgl_bayar) <= ?")
        args.append(tgl_sampai)
    # agregasi dihitung di Python agar aturan status tetap sama dengan tampilan
    tag_rows = conn.execute(
        f"""SELECT t.id, t.pokok, t.denda, t.petugas_id,
                   b.status, b.metode, b.jumlah_diterima, b.tgl_bayar, b.nomor_kwitansi
            FROM tagihan t
            LEFT JOIN pembayaran b ON b.tagihan_id = t.id AND b.void = 0
            WHERE {' AND '.join(where)}""", args).fetchall()
    agg: dict[int, dict] = {}
    seen_tag: set[tuple[int, int]] = set()
    for r in tag_rows:
        key = int(r["petugas_id"] or 0)
        a = agg.setdefault(key, {
            "petugas_id": key, "blok": "", "nama": "", "target_sppt": 0, "lunas_sppt": 0,
            "proses_sppt": 0, "belum_sppt": 0, "target_nilai": 0, "lunas_nilai": 0,
            "via_petugas": 0, "via_petugas_rp": 0, "bayar_sendiri": 0, "bayar_sendiri_rp": 0,
            "menunggak": 0, "kwitansi": 0, "kwitansi_rp": 0, "di_tangan": 0, "di_tangan_rp": 0,
        })
        tag_seen = (key, int(r["id"]))
        if tag_seen in seen_tag:
            continue
        seen_tag.add(tag_seen)
        a["target_sppt"] += 1
        a["target_nilai"] += int(r["pokok"] or 0) + int(r["denda"] or 0)
        st = r["status"]
        if st == "terverifikasi":
            a["lunas_sppt"] += 1
            a["lunas_nilai"] += int(r["jumlah_diterima"] or 0)
            if r["metode"] == "petugas":
                a["via_petugas"] += 1
                a["via_petugas_rp"] += int(r["jumlah_diterima"] or 0)
            else:
                a["bayar_sendiri"] += 1
                a["bayar_sendiri_rp"] += int(r["jumlah_diterima"] or 0)
        elif st in ("menunggu_billing", "terkirim", "belum_tagihan"):
            a["proses_sppt"] += 1
        else:
            a["belum_sppt"] += 1
        if r["nomor_kwitansi"]:
            a["kwitansi"] += 1
            a["kwitansi_rp"] += int(r["jumlah_diterima"] or 0)
            if st != "terverifikasi":          # uang sudah diterima, belum lunas di eSPPT
                a["di_tangan"] += 1
                a["di_tangan_rp"] += int(r["jumlah_diterima"] or 0)
    names = {int(p["id"]): p for p in daftar_petugas(conn)}
    for key, a in agg.items():
        p = names.get(key)
        if p:
            a["blok"], a["nama"] = p["blok"], p["nama"]
        a["persen_sppt"] = F.persen(a["lunas_sppt"], a["target_sppt"])
        a["persen_rp"] = F.persen(a["lunas_nilai"], a["target_nilai"])
        a["proses_nilai"] = a["target_nilai"] - a["lunas_nilai"]
        a["kekurangan_nilai"] = a["proses_nilai"]
    return sorted(agg.values(), key=lambda x: (x["blok"] or "").lower())


def ringkasan(conn, tahun: str, petugas_id: int | None = None) -> dict:
    rows = laporan_blok(conn, tahun)
    tot = {k: 0 for k in ("target_sppt", "lunas_sppt", "proses_sppt", "belum_sppt", "target_nilai",
                          "lunas_nilai", "via_petugas", "via_petugas_rp", "bayar_sendiri",
                          "bayar_sendiri_rp", "kwitansi", "kwitansi_rp", "di_tangan", "di_tangan_rp",
                          "kekurangan_nilai")}
    for r in rows:
        for k in tot:
            tot[k] += r[k]
    tot["persen_sppt"] = F.persen(tot["lunas_sppt"], tot["target_sppt"])
    tot["persen_rp"] = F.persen(tot["lunas_nilai"], tot["target_nilai"])
    tot["rows"] = [r for r in rows if not petugas_id or r["petugas_id"] == petugas_id]
    return tot


def rekap_kwitansi(conn, tahun: str, petugas_id: int | None, tgl_dari: str, tgl_sampai: str) -> list[dict]:
    where = ["t.tahun_pajak = ?", "b.void = 0", "b.nomor_kwitansi IS NOT NULL"]
    args: list = [tahun]
    if petugas_id:
        where.append("b.petugas_id = ?")
        args.append(petugas_id)
    if tgl_dari:
        where.append("DATE(b.tgl_bayar) >= ?")
        args.append(tgl_dari)
    if tgl_sampai:
        where.append("DATE(b.tgl_bayar) <= ?")
        args.append(tgl_sampai)
    sql = f"""SELECT b.*, t.nop, t.nama_wp, t.alamat_objek, t.pokok, t.denda, t.tahun_pajak,
                     p.blok, p.nama AS petugas_nama
              FROM pembayaran b JOIN tagihan t ON t.id=b.tagihan_id
              LEFT JOIN petugas p ON p.id=b.petugas_id
              WHERE {' AND '.join(where)} ORDER BY b.tgl_bayar, b.nomor_kwitansi"""
    return [dict(r) for r in conn.execute(sql, args)]


def selisih_kwitansi_vs_setoran(conn, tahun: str, tgl_dari: str = "", tgl_sampai: str = "") -> list[dict]:
    """Kwitansi yang diterbitkan petugas tapi statusnya belum lunas di realisasi eSPPT."""
    where = ["b.void = 0", "b.nomor_kwitansi IS NOT NULL", "t.tahun_pajak = ?",
             "b.status <> 'terverifikasi'"]
    args: list = [tahun]
    if tgl_dari:
        where.append("DATE(b.tgl_bayar) >= ?")
        args.append(tgl_dari)
    if tgl_sampai:
        where.append("DATE(b.tgl_bayar) <= ?")
        args.append(tgl_sampai)
    sql = f"""SELECT b.id, b.nomor_kwitansi, b.tgl_bayar, b.status, b.billing_code, b.jumlah_diterima,
                     t.nop, t.nama_wp, p.blok
              FROM pembayaran b JOIN tagihan t ON t.id=b.tagihan_id
              LEFT JOIN petugas p ON p.id=b.petugas_id
              WHERE {' AND '.join(where)} ORDER BY b.tgl_bayar"""
    return [dict(r) for r in conn.execute(sql, args)]


def realisasi_tanpa_kwitansi(conn, tahun: str, tgl_dari: str = "", tgl_sampai: str = "") -> list[dict]:
    where = ["r.tahun_pajak = ?", "(r.matched_pembayaran_id IS NULL OR r.matched_pembayaran_id = 0)"]
    args: list = [tahun]
    if tgl_dari:
        where.append("DATE(r.tgl_bayar) >= ?")
        args.append(tgl_dari)
    if tgl_sampai:
        where.append("DATE(r.tgl_bayar) <= ?")
        args.append(tgl_sampai)
    sql = f"""SELECT r.*, t.id AS tagihan_id FROM realisasi r
              LEFT JOIN tagihan t ON t.nop = r.nop AND t.tahun_pajak = r.tahun_pajak
              WHERE {' AND '.join(where)} ORDER BY r.tgl_bayar DESC LIMIT 500"""
    return [dict(r) for r in conn.execute(sql, args)]


# --------------------------------------------------------- sinkron otomatis (bg)

def mulai_sinkron(args: dict, mode: str = "full") -> bool:
    with SINKRON["lock"]:
        if SINKRON["berjalan"]:
            return False
        SINKRON["berjalan"] = True
        SINKRON["selesai"] = None
        SINKRON["log"] = []
        SINKRON["sudah"] = 0
        SINKRON["total"] = 0
        SINKRON["hasil"] = {"mode": mode}
        SINKRON["mode"] = mode
    t = threading.Thread(target=_kerja_sinkron, kwargs={**args, "mode": mode}, daemon=True)
    t.start()
    return True


def _kerja_sinkron(tahun: str = "", user: str = "-", buat_billing: bool = True,
                   tarik_realisasi: bool = True, tgl_dari: str = "", tgl_sampai: str = "",
                   mode: str = "manual"):
    conn = connect()
    try:
        from .esppt import EspptClient

        s = get_settings(conn)
        client = EspptClient(s, log=lambda *a: _sink_push(str(a)))
        if not client.enabled:
            raise RuntimeError("Otomatisasi eSPPT belum diaktifkan (menu Pengaturan).")
        _sink_push(f"Login ke eSPPT sebagai '{client.user}' ...")
        lr = client.login(force=True)
        if not lr.ok:
            raise RuntimeError("Login eSPPT gagal: " + (lr.error or "tidak ada respons"))
        _sink_push(f"Login OK ({client.login_info})", "ok")

        if buat_billing:
            antrean = daftar_antrean_billing(conn, tahun)
            with SINKRON["lock"]:
                SINKRON["total"] += len(antrean)
            _sink_push(f"Antrean billing: {len(antrean)} NOP.")
            for it in antrean:
                r = client.buat_billing(it["nop"], it["tahun_pajak"], it["denda"] or 0)
                _log_api(conn, it["id"], "billing", r.url, "", r.status, r.text or r.error, r.ok)
                if r.ok:
                    conn.execute("UPDATE pembayaran SET status='terkirim', billing_code=?, "
                                 "tgl_billing=?, updated_at=? WHERE id=?",
                                 (r.text, dt.date.today().isoformat(), now(), it["id"]))
                    _sink_push(f"{it['nop']} → billing {r.text}", "ok")
                else:
                    _sink_push(f"{it['nop']} → GAGAL: {r.error}", "err")
                time.sleep(client.delay)

        if tarik_realisasi:
            with SINKRON["lock"]:
                SINKRON["total"] += 1
            _sink_push("Mengunduh data realisasi ...")
            r = client.ambil_realisasi(tahun, tgl_dari, tgl_sampai)
            if not r.ok:
                raise RuntimeError("Ambil data realisasi gagal: " + (r.error or f"HTTP {r.status}"))
            rows = client.parse_realisasi(r)
            _sink_push(f"Baris realisasi terbaca: {len(rows)}")
            if rows:
                stat = impor_realisasi(conn, rows, "sinkron otomatis", "sistem")
                conn.commit()
                with SINKRON["lock"]:
                    SINKRON["hasil"].update(stat)
                _sink_push(f"Verifikasi lunas {stat['verifikasi']} NOP, "
                           f"bayar sendiri {stat['bayar_sendiri']} NOP.", "ok")
            with SINKRON["lock"]:
                SINKRON["sudah"] += 1

        conn.commit()
        with SINKRON["lock"]:
            SINKRON["hasil"]["sukses"] = True
            SINKRON["selesai"] = now()
        _sink_push("Selesai.", "ok")
    except Exception as e:                      # kesalahan apa pun ditampilkan ke user
        conn.rollback()
        _sink_push(f"{type(e).__name__}: {e}", "err")
        _sink_push(traceback.format_exc(limit=3), "err")
        with SINKRON["lock"]:
            SINKRON["hasil"]["sukses"] = False
            SINKRON["hasil"]["pesan"] = str(e)
            SINKRON["selesai"] = now()
    finally:
        with SINKRON["lock"]:
            SINKRON["berjalan"] = False
        conn.close()
