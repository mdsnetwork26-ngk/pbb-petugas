"""Login, peran (admin / petugas), dan perlindungan CSRF."""
from __future__ import annotations

import functools
import hmac
import secrets

from flask import abort, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from .db import connect

PUBLIC_ENDPOINTS = {"auth.login", "static", "app.cek_kwitansi_publik"}


def user_aktual() -> dict | None:
    if "uid" not in session:
        return None
    conn = connect()
    try:
        row = conn.execute("SELECT * FROM users WHERE id=? AND aktif=1", (session["uid"],)).fetchone()
        if not row:
            return None
        u = dict(row)
        if u.get("petugas_id"):
            p = conn.execute("SELECT * FROM petugas WHERE id=?", (u["petugas_id"],)).fetchone()
            u["petugas"] = dict(p) if p else None
        else:
            u["petugas"] = None
        return u
    finally:
        conn.close()


def csrf_token() -> str:
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(24)
    return session["csrf"]


def csrf_ok() -> bool:
    sent = request.form.get("_csrf") or request.headers.get("X-CSRF-Token") or ""
    return bool(session.get("csrf")) and hmac.compare_digest(str(sent), str(session["csrf"]))


def init_app(app) -> None:
    @app.before_request
    def _guard():
        g.user = None
        if request.endpoint in PUBLIC_ENDPOINTS or request.endpoint is None:
            return None
        u = user_aktual()
        if u is None:
            if request.path.startswith("/api/"):
                return abort(401, description="Sesi berakhir.")
            session["after_login"] = request.full_path if request.query_string else request.path
            return redirect(url_for("auth.login", next=request.path))
        g.user = u
        g.csrf = csrf_token()
        if request.method in ("POST", "PUT", "PATCH", "DELETE") and not csrf_ok():
            return abort(400, description="Token CSRF tidak cocok — muat ulang halaman lalu coba lagi.")
        return None

    @app.context_processor
    def _inject():
        pengguna = getattr(g, "user", None) or {}
        return {
            "u": pengguna or None,
            "csrf_token": getattr(g, "csrf", ""),
            "is_admin": bool(pengguna.get("role") == "admin"),
            "petugas_id": pengguna.get("petugas_id"),
        }


def role_perlu(role: str):
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*a, **kw):
            if not g.get("user"):
                return redirect(url_for("auth.login"))
            if role == "admin" and g.user.get("role") != "admin":
                abort(403, description="Menu ini khusus admin (kepala desa/lurah atau bendahara pembina).")
            return fn(*a, **kw)
        return wrapper
    return deco


admin_perlu = role_perlu("admin")


# ------------------------------------------------------------------------- routes
from flask import Blueprint  # noqa: E402

bp = Blueprint("auth", __name__)


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET" and session.get("uid") and user_aktual():
        return redirect(url_for("app.dashboard"))
    error = ""
    if request.method == "POST":
        uname = (request.form.get("username") or "").strip()
        pw = request.form.get("password") or ""
        conn = connect()
        try:
            row = conn.execute("SELECT * FROM users WHERE username=? AND aktif=1", (uname,)).fetchone()
        finally:
            conn.close()
        if row and check_password_hash(row["password_hash"], pw):
            session.clear()
            session["uid"] = row["id"]
            session.permanent = True
            nxt = session.get("after_login") or (
                url_for("app.data_pajak") if row["role"] == "petugas" else url_for("app.dashboard"))
            session.pop("after_login", None)
            return redirect(nxt)
        error = "User atau password salah."
    return render_template("auth/login.html", error=error)


@bp.route("/logout")
def logout():
    session.clear()
    flash("Anda sudah keluar.", "info")
    return redirect(url_for("auth.login"))


@bp.route("/ganti-password", methods=["GET", "POST"])
def ganti_password():
    if request.method == "POST":
        if not check_password_hash(_hash_lama(), request.form.get("lama") or ""):
            flash("Password lama tidak cocok.", "danger")
            return redirect(url_for("auth.ganti_password"))
        baru = request.form.get("baru") or ""
        if request.form.get("baru2") != baru:
            flash("Penulisan password baru dua kali tidak sama.", "danger")
            return redirect(url_for("auth.ganti_password"))
        if len(baru) < 6:
            flash("Password baru minimal 6 karakter.", "danger")
            return redirect(url_for("auth.ganti_password"))
        conn = connect()
        try:
            conn.execute("UPDATE users SET password_hash=? WHERE id=?",
                         (generate_password_hash(baru), g.user["id"]))
            conn.commit()
        finally:
            conn.close()
        flash("Password berhasil diganti.", "ok")
        return redirect(url_for("app.dashboard"))
    return render_template("auth/password.html")


def _hash_lama() -> str:
    conn = connect()
    try:
        return conn.execute("SELECT password_hash FROM users WHERE id=?", (g.user["id"],)).fetchone()["password_hash"]
    finally:
        conn.close()
