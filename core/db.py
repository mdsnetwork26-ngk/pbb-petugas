"""Fungsi util: skema database, default pengaturan, backup otomatis, dan akses settings.

Semua data aplikasi disimpan di SATU file SQLite (data/pbb.db) sehingga mudah dibackup,
dipindah antar komputer, atau dibuka ulang setelah listrik padam.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
from pathlib import Path

# ---------------------------------------------------------------- lokasi berkas

BASE_DIR = Path(__file__).resolve().parent.parent          # .../pbb-petugas
DATA_DIR = Path(os.environ.get("PBB_DATA_DIR", BASE_DIR / "data"))
BACKUP_DIR = DATA_DIR / "backup"
UPLOAD_DIR = DATA_DIR / "upload"
DATA_AUTO_DIR = DATA_DIR / "auto"
DB_PATH = DATA_DIR / "pbb.db"

for _d in (DATA_DIR, BACKUP_DIR, UPLOAD_DIR, DATA_AUTO_DIR / "sudah-diproses"):
    _d.mkdir(parents=True, exist_ok=True)

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT UNIQUE NOT NULL,
  password_hash TEXT NOT NULL,
  nama TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'admin',          -- admin | petugas
  petugas_id INTEGER,
  aktif INTEGER NOT NULL DEFAULT 1,
  created_at TEXT
);

CREATE TABLE IF NOT EXISTS petugas(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  nama TEXT NOT NULL,
  blok TEXT NOT NULL,
  wilayah TEXT DEFAULT '',
  telepon TEXT DEFAULT '',
  nip TEXT DEFAULT '',
  jabatan TEXT DEFAULT 'Petugas Pemungut PBB-P2',
  aktif INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS import_batch(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  jenis TEXT NOT NULL,                          -- ketetapan | realisasi
  nama_file TEXT,
  tahun_pajak TEXT,
  info TEXT,
  stat TEXT,
  created_at TEXT,
  created_by TEXT
);

CREATE TABLE IF NOT EXISTS tagihan(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  tahun_pajak TEXT NOT NULL,
  nop TEXT NOT NULL,
  nama_wp TEXT NOT NULL DEFAULT '',
  alamat_objek TEXT DEFAULT '',
  alamat_wp TEXT DEFAULT '',
  luas_bumi REAL DEFAULT 0,
  luas_bangunan REAL DEFAULT 0,
  pokok INTEGER NOT NULL DEFAULT 0,             -- PBB terutang (rupiah, dibulatkan)
  denda INTEGER NOT NULL DEFAULT 0,
  petugas_id INTEGER,
  kelurahan TEXT DEFAULT '',
  kecamatan TEXT DEFAULT '',
  keterangan TEXT DEFAULT '',
  aktif INTEGER NOT NULL DEFAULT 1,
  batch_id INTEGER,
  created_at TEXT,
  updated_at TEXT,
  UNIQUE(tahun_pajak, nop)
);
CREATE INDEX IF NOT EXISTS ix_tagihan_nop ON tagihan(nop);
CREATE INDEX IF NOT EXISTS ix_tagihan_petugas ON tagihan(petugas_id);

CREATE TABLE IF NOT EXISTS pembayaran(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  tagihan_id INTEGER NOT NULL REFERENCES tagihan(id),
  nomor_kwitansi TEXT UNIQUE,
  tgl_bayar TEXT,                               -- YYYY-MM-DD (tanggal terima uang)
  metode TEXT NOT NULL DEFAULT 'petugas',       -- petugas | bank | pos | wp | lainnya
  jumlah_diterima INTEGER NOT NULL DEFAULT 0,
  biaya_admin INTEGER NOT NULL DEFAULT 0,
  pembayar TEXT DEFAULT '',
  status TEXT NOT NULL DEFAULT 'belum_tagihan', -- belum_tagihan|menunggu_billing|terkirim|terverifikasi
  billing_code TEXT DEFAULT '',
  tgl_billing TEXT,
  tgl_verifikasi TEXT,
  petugas_id INTEGER,
  catatan TEXT DEFAULT '',
  void INTEGER NOT NULL DEFAULT 0,
  created_at TEXT,
  updated_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_bayar_tagihan ON pembayaran(tagihan_id);
CREATE INDEX IF NOT EXISTS ix_bayar_status ON pembayaran(status);
CREATE INDEX IF NOT EXISTS ix_bayar_tgl ON pembayaran(tgl_bayar);

CREATE TABLE IF NOT EXISTS realisasi(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  nop TEXT NOT NULL,
  nama_wp TEXT DEFAULT '',
  alamat_wp TEXT DEFAULT '',
  tahun_pajak TEXT NOT NULL,
  pokok INTEGER NOT NULL DEFAULT 0,
  denda INTEGER NOT NULL DEFAULT 0,
  jumlah INTEGER NOT NULL DEFAULT 0,
  tgl_bayar TEXT,
  channel TEXT DEFAULT '',
  batch_id INTEGER,
  matched_pembayaran_id INTEGER,
  created_at TEXT,
  UNIQUE(tahun_pajak, nop, tgl_bayar, jumlah)
);
CREATE INDEX IF NOT EXISTS ix_realisasi_nop ON realisasi(nop);

CREATE TABLE IF NOT EXISTS usulan_pindah(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  tagihan_id INTEGER NOT NULL REFERENCES tagihan(id),
  petugas_lama INTEGER,
  petugas_baru INTEGER NOT NULL,
  alasan TEXT DEFAULT '',
  status TEXT NOT NULL DEFAULT 'menunggu',          -- menunggu | disetujui | ditolak
  diajukan_oleh TEXT DEFAULT '',
  diajukan_pada TEXT,
  diputuskan_pada TEXT,
  catatan_admin TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_usulan_tagihan ON usulan_pindah(tagihan_id);
CREATE INDEX IF NOT EXISTS ix_usulan_status ON usulan_pindah(status);

CREATE TABLE IF NOT EXISTS billing_log(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  pembayaran_id INTEGER,
  aksi TEXT,
  url TEXT,
  req TEXT,
  http_status INTEGER,
  resp TEXT,
  ok INTEGER,
  created_at TEXT
);

CREATE TABLE IF NOT EXISTS settings(
  key TEXT PRIMARY KEY,
  value TEXT
);
"""

# Nilai default seluruh pengaturan (ubah lewat menu Pengaturan di aplikasi).
DEFAULTS: dict[str, str] = {
    # ---- identitas & cetak ----
    "wilayah_desa": "KETAMAN GANGGO HILIR",
    "wilayah_kecamatan": "NGANJUK",
    "wilayah_kabupaten": "Kabupaten Nganjuk",
    "alamat_kantor": "",
    "nama_petugas_bendahara": ".......................",
    "nip_petugas": "",
    "jabatan_penandatangan": "Petugas Pemungut PBB-P2",
    "nama_lurah": ".......................",
    "jabatan_lurah": "Lurah / Kepala Desa",
    "nip_lurah": "",
    "cuti": "1",
    "no_rekening": "",
    "catatan_kwitansi": "Simpan kwitansi ini sebagai bukti bayar yang sah.",
    # ---- denda & jatuh tempo ----
    "jatuh_tempo_bulan": "8",        # bulan jatuh tempo SPPT (8 = akhir Agustus); denda berjalan bulan berikutnya
    "denda_persen": "1",             # persen dari pokok per bulan terlampaui (ketetapan daerah: 1%)
    "denda_maks_bulan": "24",        # batas akumulasi denda (bulan)
    # ---- sinkronisasi eSPPT ----
    "esppt_enabled": "0",
    "esppt_base_url": "https://esppt.nganjukkab.go.id",
    "esppt_user": "",
    "esppt_password": "",
    "esppt_verify_ssl": "0",
    "esppt_http_timeout": "30",
    "esppt_login_path": "/login",
    "esppt_realisasi_path": "/realisasi_rekap_lurah",
    "esppt_realisasi_params": '{"tahun": "{tahun}", "page": "1", "limit": "2000"}',
    "esppt_realisasi_regex": "",
    "esppt_realisasi_template": "",
    "esppt_tagihan_path": "/tagihanpbb",
    "esppt_billing_template": "",
    "esppt_billing_regex": r"(?:[0-9]{12,20})",
    "esppt_billing_dry_run": "1",
    "esppt_delay_ms": "900",
    "auto_lunas_realisasi": "1",
    "auto_folder": "1",
    "auto_token": "",
    "skip_billing": "0",
    "auto_lunas_bayar_sendiri": "1",
}


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30, detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def init_db(app=None) -> None:
    """Buat tabel (idempoten), isi nilai default, buat akun admin awal, backup harian."""
    conn = connect()
    try:
        conn.executescript(SCHEMA)
        for k, v in DEFAULTS.items():
            conn.execute("INSERT OR IGNORE INTO settings(key, value) VALUES(?, ?)", (k, v))
        # migrasi sekali (2026): denda bawaan lama 2%/bulan -> 1%/bulan sesuai
        # ketetapan daerah. Hanya berjalan sekali sepanjang umur database; nilai
        # yang sudah sengaja diubah admin (bukan "2") tidak disentuh.
        if not conn.execute(
                "SELECT 1 FROM settings WHERE key='denda_persen_migrasi_1'").fetchone():
            cur = conn.execute("SELECT value FROM settings WHERE key='denda_persen'").fetchone()
            if cur and str(cur["value"]).strip() == "2":
                conn.execute("UPDATE settings SET value='1' WHERE key='denda_persen'")
            conn.execute(
                "INSERT INTO settings(key,value) VALUES('denda_persen_migrasi_1','1')")
        _seed_if_empty(conn)
        conn.commit()
    finally:
        conn.close()
    daily_backup()


def _seed_if_empty(conn: sqlite3.Connection) -> None:
    if conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"] == 0:
        from werkzeug.security import generate_password_hash

        conn.execute(
            "INSERT INTO users(username,password_hash,nama,role,created_at) VALUES(?,?,?,?,?)",
            ("admin", generate_password_hash("admin123"), "Administrator", "admin", now()),
        )


def now() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ------------------------------------------------------------------- settings

def get_settings(conn=None) -> dict[str, str]:
    close = False
    if conn is None:
        conn, close = connect(), True
    try:
        rows = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM settings")}
    finally:
        if close:
            conn.close()
    out = dict(DEFAULTS)
    out.update({k: (v if v is not None else "") for k, v in rows.items()})
    return out


def set_settings(conn, data: dict[str, str]) -> None:
    for k, v in data.items():
        conn.execute(
            "INSERT INTO settings(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (k, v),
        )


def setting_int(s: dict, key: str, default: int = 0) -> int:
    try:
        return int(str(s.get(key, default)).strip() or default)
    except ValueError:
        return default


def setting_bool(s: dict, key: str) -> bool:
    return str(s.get(key, "")).strip().lower() in ("1", "true", "ya", "on")


# --------------------------------------------------------------------- backup

def daily_backup() -> Path | None:
    """Salin DB ke data/backup/pbb-YYYY-MM-DD.db maksimal sekali per hari."""
    if not DB_PATH.exists():
        return None
    dest = BACKUP_DIR / f"pbb-{dt.date.today():%Y-%m-%d}.db"
    if dest.exists():
        return None
    dest.parent.mkdir(parents=True, exist_ok=True)
    src = connect()
    try:
        dst = sqlite3.connect(dest)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    olds = sorted(BACKUP_DIR.glob("pbb-*.db"))
    for p in olds[:-60]:                      # simpan maksimal 60 cadangan harian
        try:
            p.unlink()
        except OSError:
            pass
    return dest


def db_size_bytes() -> int:
    return DB_PATH.stat().st_size if DB_PATH.exists() else 0


def json_dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)
