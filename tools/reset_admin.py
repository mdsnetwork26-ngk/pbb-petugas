"""Kembalikan password akun admin menjadi 'admin123'.

Jalankan dari folder aplikasi:
    .venv\\Scripts\\python tools\\reset_admin.py
Gunakan bila password admin lupa, lalu SEGERA ganti lagi setelah masuk.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from werkzeug.security import generate_password_hash  # noqa: E402

from core.db import connect, init_db  # noqa: E402


def main() -> int:
    init_db()
    conn = connect()
    try:
        row = conn.execute("SELECT id, username FROM users WHERE role='admin' ORDER BY id LIMIT 1").fetchone()
        if not row:
            print("Akun admin tidak ditemukan; membuat baru...")
            conn.execute(
                "INSERT INTO users(username,password_hash,nama,role,aktif,created_at) "
                "VALUES('admin',?,'Administrator','admin',1,datetime('now','localtime'))",
                (generate_password_hash("admin123"),))
        else:
            conn.execute("UPDATE users SET password_hash=?, aktif=1 WHERE id=?",
                         (generate_password_hash("admin123"), row["id"]))
        conn.commit()
    finally:
        conn.close()
    print("Password admin sekarang: admin / admin123  — ganti segera setelah masuk.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
