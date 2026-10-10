"""Simpan tampilan halaman aplikasi sebagai gambar PNG (untuk ditinjau tanpa membuka browser).

Jalankan:  .venv\\Scripts\\python tools\\pratinjau.py [alamat]
Default alamat: http://127.0.0.1:8088   (aplikasi harus sedang jalan)
Hasil:      pratinjau/*.png  +  pratinjau/index.html
Butuh:     weasyprint + pymupdf (hanya untuk berkas gambar; bukan bagian aplikasi)
           .venv\\Scripts\\pip install weasyprint pymupdf
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

HALAMAN = [
    ("beranda", "/", "login"),
    ("data-ketetapan", "/data", None),
    ("formulir-bayar", "/bayar/{tag_id}", None),
    ("kwitansi-a4", "/kwitansi/{pid}", "cetak"),
    ("antrean-billing", "/billing", None),
    ("laporan-blok", "/laporan", "print"),
    ("papan-progres", "/monitor", "print"),
    ("transaksi", "/transaksi", None),
    ("pengaturan-esppt", "/pengaturan?tab=esppt", None),
    ("impor", "/impor", None),
]


def ambil(base: str, path: str, cookie: str | None = None) -> str:
    cmd = ["curl", "-s", "-L", f"{base}{path}"]
    if cookie:
        cmd[3:3] = ["-b", cookie]
    return subprocess.run(cmd, capture_output=True, text=True).stdout


def token_login(base: str, jar: Path) -> str:
    html = ambil(base, "/login")
    tok = re.search(r'name="_csrf" value="([^"]*)"', html)
    return tok.group(1) if tok else ""


def login(base: str, jar: Path, user="admin", pw="admin123") -> None:
    tok = token_login(base, jar)
    subprocess.run(["curl", "-s", "-c", str(jar), "-o", "/dev/null", "-X", "POST",
                    f"{base}/login", "--data", f"_csrf={tok}&username={user}&password={pw}"],
                   capture_output=True)


def nilai_url(base: str, jar: Path, path: str) -> str:
    if "{" not in path:
        return path
    from core.db import connect

    c = connect()
    try:
        tahun = str(__import__("datetime").date.today().year)
        tag = c.execute("SELECT id FROM tagihan WHERE tahun_pajak=? ORDER BY id LIMIT 1",
                        (tahun,)).fetchone()
        byr = c.execute("SELECT id FROM pembayaran WHERE nomor_kwitansi IS NOT NULL ORDER BY id DESC "
                        "LIMIT 1").fetchone()
    finally:
        c.close()
    path = path.replace("{tag_id}", str(tag["id"]) if tag else "1")
    path = path.replace("{pid}", str(byr["id"]) if byr else "1")
    if not byr and "kwitansi" in path:
        return ""
    return path


def main() -> int:
    base = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8088"
    out = BASE / "pratinjau"
    out.mkdir(exist_ok=True)
    jar = Path("/tmp/pbb-pratinjau-cookie.txt")
    login(base, jar)

    try:
        from weasyprint import HTML
        import pymupdf
    except ImportError:
        print("Pasang dulu:  .venv\\Scripts\\pip install weasyprint pymupdf")
        return 1

    dibuat = []
    for nama, path, buang in HALAMAN:
        url = nilai_url(base, jar, path)
        if not url:
            print(f"  lewati {nama}: belum ada datanya")
            continue
        html = ambil(base, url, str(jar))
        if buang == "cetak":
            html = re.sub(r"\s*<div class=\"row tanpa-cetak[\"'][^>]*>.*?</div>\s*</div>", "</div>", html,
                          count=1, flags=re.S)
        if not html.strip():
            print(f"  lewati {nama}: halaman kosong")
            continue
        (out / f"{nama}.html").write_text(html, encoding="utf-8")
        pdf = out / f"{nama}.pdf"
        try:
            HTML(string=html, base_url=str(out)).write_pdf(str(pdf), options={"media": "print"})
            dok = pymupdf.open(str(pdf))
            dok[0].get_pixmap(dpi=115).save(str(out / f"{nama}.png"))
            dok.close()
            dibuat.append(nama)
            print(f"  ✓ {nama}")
        except Exception as e:                        # jangan gagal total karena satu halaman
            print(f"  ! {nama}: {type(e).__name__}: {e}")

    indeks = ["<meta charset='utf-8'><title>Pratinjau aplikasi</title>",
              "<style>body{font:14px system-ui;margin:2rem;background:#f4f6f9}"
              "h1{font-size:1.2rem}figure{background:#fff;margin:0 0 1.5rem;padding:1rem;"
              "border:1px solid #e2e8f0;border-radius:10px}img{max-width:100%}"
              "figcaption{font-weight:600;margin-bottom:.5rem}</style><h1>Tampilan halaman (A4/web)</h1>"]
    for n in dibuat:
        indeks.append(f"<figure><figcaption>{n}</figcaption><img src='{n}.png'></figure>")
    (out / "index.html").write_text("\n".join(indeks), encoding="utf-8")
    print(f"\nSelesai: {len(dibuat)} halaman -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
