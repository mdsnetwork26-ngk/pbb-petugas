"""Adapter ke eSPPT-SIMPBB Bapenda Kab. Nganjuk (https://esppt.nganjukkab.go.id).

Kenapa adapter ini "bisa diatur", bukan dipatok mati:
  - eSPPT tidak menyediakan API publik; halaman datanya HTML hasil render PHP/Zend.
  - Setiap perubahan tampilan di sisi Bapenda bisa merusak skrip otomatis.
Maka dipakai 3 lapis strategi (semuanya ada di menu Pengaturan):
  1. mode OFF / dry-run  -> aplikasi tidak menyentuh eSPPT sama sekali (default).
  2. endpoint JSON/URL   -> bila Bapenda punya endpoint data (diisi manual kalau ada).
  3. replay cURL         -> tempel perintah cURL dari DevTools browser (Network > Copy as cURL),
                            aplikasi tinggal mengganti NOP/Tahun dan membaca kode billing.

Kredensial disimpan di file database lokal (data/pbb.db) dan bisa dioverride lewat
variabel lingkungan PBB_ESPPT_USER / PBB_ESPPT_PASSWORD.
"""
from __future__ import annotations

import http.cookiejar
import json
import os
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36 PBB-Loket/1.0")


@dataclass
class Resp:
    ok: bool
    status: int | None
    text: str = ""
    error: str = ""
    url: str = ""

    @property
    def json(self):
        try:
            return json.loads(self.text)
        except Exception:
            return None


@dataclass
class RequestSpec:
    """Deskripsi satu request HTTP: dibangun dari template atau dari cURL."""
    url: str = ""
    method: str = "POST"
    headers: dict = field(default_factory=dict)
    body: str = ""
    body_kind: str = "form"          # form | json | raw
    extract_regex: str = ""

    def filled(self, mapping: dict) -> "RequestSpec":
        def sub(s: str) -> str:
            if not s:
                return s
            out = s
            for k, v in mapping.items():
                out = out.replace("{" + k + "}", str(v))
            return out

        return RequestSpec(
            url=sub(self.url),
            method=self.method,
            headers={sub(k): sub(v) for k, v in self.headers.items()},
            body=sub(self.body),
            body_kind=self.body_kind,
            extract_regex=self.extract_regex,
        )


CURL_TOKEN = re.compile(r"""(?:^|\s)(--curl|-X|--request|--url|-H|--header|-b|--cookie|--data-raw|--data-binary|--data|--compressed|-A|--user-agent)\s+(?:'([^']*)'|"([^"]*)"|(\S+))""")


def parse_curl(cmd: str, base_url: str = "") -> RequestSpec:
    """Ubah 'Copy as cURL' dari browser menjadi RequestSpec."""
    spec = RequestSpec()
    url_found = ""
    tokens = CURL_TOKEN.findall(cmd or "")
    data_chunks: list[str] = []
    for i, flag in enumerate(tokens):
        val = next((g for g in flag[1:] if g), "")
        f = flag[0]
        if f in ("--curl",):
            continue
        if f in ("-X", "--request"):
            spec.method = val.upper()
        elif f in ("--url",):
            url_found = val
        elif f in ("-H", "--header"):
            if ":" in val:
                k, v = val.split(":", 1)
                spec.headers[k.strip()] = v.strip()
        elif f in ("-b", "--cookie"):
            spec.headers["Cookie"] = val
        elif f in ("-A", "--user-agent"):
            spec.headers["User-Agent"] = val
        elif f in ("--data-raw", "--data-binary", "--data"):
            data_chunks.append(val)
    m = re.search(r"curl\s+['\"]?([^'\"\s]+)", cmd or "")
    if url_found:
        spec.url = url_found
    elif m:
        spec.url = m.group(1)
    if base_url and spec.url and not spec.url.startswith("http"):
        spec.url = base_url.rstrip("/") + "/" + spec.url.lstrip("/")
    body = "&".join(c.lstrip("-").lstrip("'\"") for c in data_chunks if c) if data_chunks else ""
    body = re.sub(r"^[\-]{1,2}\s*", "", body)
    if data_chunks:
        raw = "\n".join(data_chunks)
        spec.body = raw
        if raw.strip().startswith("{") or "json" in spec.headers.get("Content-Type", "").lower():
            spec.body_kind = "json"
    if not spec.method:
        spec.method = "POST" if spec.body else "GET"
    return spec


class EspptClient:
    """Klien HTTP ringan berbasis urllib (tanpa dependency tambahan)."""

    def __init__(self, settings: dict, log=None):
        s = settings
        self.enabled = str(s.get("esppt_enabled", "0")) in ("1", "true", "ya")
        self.dry_run = str(s.get("esppt_billing_dry_run", "1")) in ("1", "true", "ya")
        self.base = (s.get("esppt_base_url") or "https://esppt.nganjukkab.go.id").rstrip("/")
        self.user = os.environ.get("PBB_ESPPT_USER") or s.get("esppt_user", "")
        self.password = os.environ.get("PBB_ESPPT_PASSWORD") or s.get("esppt_password", "")
        self.timeout = _as_int(s.get("esppt_http_timeout"), 30)
        self.verify_ssl = str(s.get("esppt_verify_ssl", "0")) in ("1", "true", "ya")
        self.delay = _as_int(s.get("esppt_delay_ms"), 900) / 1000.0
        self.login_path = s.get("esppt_login_path") or "/login"
        self.realisasi_path = s.get("esppt_realisasi_path") or "/realisasi_rekap_lurah"
        self.realisasi_params = s.get("esppt_realisasi_params") or ""
        self.realisasi_regex = s.get("esppt_realisasi_regex") or ""
        self.tagihan_path = s.get("esppt_tagihan_path") or "/tagihanpbb"
        self._log = log or (lambda *a, **k: None)
        self._jar = http.cookiejar.CookieJar()
        ctx = ssl.create_default_context()
        if not self.verify_ssl:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self._jar),
            urllib.request.HTTPSHandler(context=ctx),
        )
        opener.addheaders = [("User-Agent", UA), ("Accept-Language", "id,en;q=0.8")]
        self._opener = opener
        self.logged_in = False
        self.login_info = ""
        tmpl = (s.get("esppt_billing_template") or "").strip()
        if tmpl.lower().lstrip().startswith(("curl ", "curl\n")) or "--data" in tmpl:
            self._template = parse_curl(tmpl, self.base)
        else:
            self._template = RequestSpec(url=tmpl)
        if s.get("esppt_billing_regex"):
            self._template.extract_regex = s["esppt_billing_regex"]
        rtmpl = (s.get("esppt_realisasi_template") or "").strip()
        self._realisasi_template = None
        if rtmpl:
            self._realisasi_template = (parse_curl(rtmpl, self.base)
                                        if (rtmpl.lower().lstrip().startswith("curl") or "--data" in rtmpl)
                                        else RequestSpec(url=rtmpl))
        self._extra_headers = {}

    # ------------------------------------------------------------- HTTP dasar

    def url(self, path: str) -> str:
        if not path:
            return self.base
        if path.startswith("http"):
            return path
        return self.base + "/" + path.lstrip("/")

    def http(self, url: str, data=None, method: str = "GET", headers: dict | None = None,
             raw_body: bool = False) -> Resp:
        hdrs = {"User-Agent": UA, "Accept": "text/html,application/json;q=0.9,*/*;q=0.8"}
        hdrs.update(self._extra_headers)
        hdrs.update(headers or {})
        body = None
        if data is not None:
            if raw_body:
                body = data.encode() if isinstance(data, str) else data
            else:
                body = urllib.parse.urlencode(data).encode()
                hdrs.setdefault("Content-Type", "application/x-www-form-urlencoded; charset=UTF-8")
        req = urllib.request.Request(url, data=body, headers=hdrs, method=method)
        try:
            with self._opener.open(req, timeout=self.timeout) as r:
                payload = r.read()
                text = payload.decode("utf-8", "replace")
                return Resp(ok=True, status=r.status, text=text, url=r.geturl())
        except urllib.error.HTTPError as e:
            txt = ""
            try:
                txt = e.read().decode("utf-8", "replace")
            except Exception:
                pass
            return Resp(ok=False, status=e.code, text=txt, error=f"HTTP {e.code}", url=url)
        except Exception as e:                       # DNS, timeout, SSL, koneksi diputus
            return Resp(ok=False, status=None, error=f"{type(e).__name__}: {e}", url=url)

    # ------------------------------------------------------------------ login

    def login(self, force: bool = False) -> Resp:
        if self.logged_in and not force:
            return Resp(ok=True, status=200, text="", error="", url="cache")
        if not (self.user and self.password):
            return Resp(False, None, error="User/password eSPPT belum diisi di menu Pengaturan.")
        page = self.http(self.url(self.login_path))
        csrf_headers, pre = {}, {}
        if page.ok:
            for name, value in re.findall(
                    r'<input[^>]+type="hidden"[^>]+name="([^"]+)"[^>]+value="([^"]*)"', page.text):
                pre[name] = value
            for m in re.finditer(r'<meta[^>]+name="csrf[^"]*"[^>]+content="([^"]+)"', page.text, re.I):
                csrf_headers["X-CSRF-Token"] = m.group(1)
        candidates = [
            {"username": self.user, "password": self.password},
            {"user": self.user, "pass": self.password},
            {"nis": self.user, "password": self.password},
        ]
        last = Resp(False, None, error="tidak ada percobaan login")
        for c in candidates:
            payload = dict(pre)
            payload.update(c)
            r = self.http(self.url(self.login_path), data=payload, method="POST",
                          headers=csrf_headers)
            last = r
            tetap_di_login = "login" in (r.url or "").lower()
            error_txt = bool(re.search(r"salah|tidak valid|incorrect|invalid|gagal",
                                       r.text or "", re.I))
            if r.ok and r.status not in (401, 403) and not (tetap_di_login and error_txt) \
                    and not (tetap_di_login and "username" in (r.text or "").lower()
                             and "password" in (r.text or "").lower()):
                self.logged_in = True
                self.login_info = f"payload {sorted(c)} -> HTTP {r.status} di {r.url}"
                self._log("login", "ok", r.url)
                return r
        self._log("login", "gagal", last.url)
        last.ok = False
        last.error = (last.error or "") + \
            " Login ditolak: tetap di halaman login / HTTP 401-403. Cek user & password " \
            "eSPPT, atau isi 'Path login' + template cURL di menu Pengaturan."
        return last

    def logout(self):
        self._jar.clear()
        self.logged_in = False

    # -------------------------------------------------------- cek tagihan NOP

    def cek_tagihan(self, nop: str, tahun: str = "") -> Resp:
        """Pakai halaman publik 'Cek Tagihan PBB-P2' (/tagihanpbb)."""
        payload = {"nop": nop, "nop_cek": nop}
        if tahun:
            payload["tahun"] = tahun
        r = self.http(self.url(self.tagihan_path), data=payload, method="POST")
        if not r.ok and r.status in (404, 405, 500, None):
            r = self.http(self.url(self.tagihan_path) + "?" + urllib.parse.urlencode({"nop": nop}))
        return r

    # ------------------------------------------------------------ buat billing

    def buat_billing(self, nop: str, tahun: str, denda: int = 0) -> Resp:
        spec = self._template
        if not spec.url:
            return Resp(False, None, error="Template request billing belum diisi "
                                           "(Pengaturan > 'Template request billing').")
        if self.dry_run:
            return Resp(False, None, error="Dry-run aktif: tidak ada request yang dikirim. "
                                           "Matikan 'Hanya simulasi' setelah template diuji.")
        fill = {"nop": nop, "nop_no_dot": nop.replace(".", "").replace("-", ""),
                "tahun": tahun, "denda": denda, "user": self.user}
        f = spec.filled(fill)
        body = f.body
        headers = dict(f.headers)
        if f.body_kind == "json":
            headers.setdefault("Content-Type", "application/json")
            r = self.http(f.url, data=body, method=f.method, headers=headers, raw_body=True)
        else:
            headers.setdefault("Content-Type", "application/x-www-form-urlencoded; charset=UTF-8")
            r = self.http(f.url, data=body, method=f.method, headers=headers, raw_body=True)
        if r.ok:
            code = self._extract(r.text, f.extract_regex or r"(?:[0-9]{12,20})")
            r.text = code or r.text[:400]
            r.ok = bool(code)
            if not code:
                r.error = "Request sukses tapi kode billing tidak ketemu; sesuaikan regex."
        return r

    @staticmethod
    def _extract(text: str, pattern: str) -> str:
        if not text or not pattern:
            return ""
        try:
            for m in re.finditer(pattern, text):
                val = (m.group(1) if m.groups() else m.group(0)) or ""
                val = re.sub(r"[^0-9A-Za-z-]", "", val)
                if len(val) >= 8:
                    return val
        except re.error:
            return ""
        return ""

    # --------------------------------------------------------- data realisasi

    def ambil_realisasi(self, tahun: str = "", tgl_dari: str = "", tgl_sampai: str = "",
                        halaman: int = 1) -> Resp:
        params = {}
        raw = (self.realisasi_params or "").strip()
        if raw:
            try:
                obj = json.loads(raw)
                if isinstance(obj, dict):
                    params = {k: str(v) for k, v in obj.items()}
            except json.JSONDecodeError:
                params = dict(urllib.parse.parse_qsl(raw))
        fill = {"tahun": tahun, "dari": tgl_dari, "sampai": tgl_sampai, "page": halaman,
                "halaman": halaman, "user": self.user}
        params = {k: _sub(v, fill) for k, v in params.items()}
        if tahun and "tahun" not in params:
            params["tahun"] = tahun
        if getattr(self, "_realisasi_template", None) and self._realisasi_template.url:
            f = self._realisasi_template.filled(fill)
            hdr = dict(f.headers)
            if f.body_kind == "json":
                hdr.setdefault("Content-Type", "application/json")
            elif f.body:
                hdr.setdefault("Content-Type", "application/x-www-form-urlencoded; charset=UTF-8")
            return self.http(f.url, data=f.body or None, method=f.method, headers=hdr,
                             raw_body=bool(f.body))
        url = self.url(self.realisasi_path)
        query = urllib.parse.urlencode(params) if params else ""
        return self.http(url + ("?" + query if query else ""), method="GET")

    @staticmethod
    def parse_realisasi(resp: Resp) -> list[dict]:
        """Ubah respons eSPPT (JSON API atau tabel HTML) menjadi baris realisasi."""
        rows: list[dict] = []
        if not (resp.ok or resp.text):
            return rows
        j = resp.json
        if isinstance(j, dict):
            data = j.get("data") or j.get("rows") or j.get("result") or []
            if isinstance(data, list):
                for it in data:
                    if not isinstance(it, dict):
                        continue
                    low = {re.sub(r"[^a-z0-9]", "", str(k).lower()): v for k, v in it.items()}
                    rows.append({
                        "nop": low.get("nop") or "",
                        "nama_wp": low.get("namawp") or low.get("nama") or "",
                        "alamat_wp": low.get("alamatwp") or low.get("alamat") or "",
                        "tahun_pajak": low.get("tahunpajak") or low.get("tahun") or "",
                        "pokok": low.get("pokok") or 0,
                        "denda": low.get("denda") or 0,
                        "jumlah": low.get("jumlahpembayaran") or low.get("jumlah") or 0,
                        "tgl_bayar": low.get("tglbayar") or low.get("tanggalpembayaran") or "",
                        "channel": low.get("channel") or low.get("media") or "",
                    })
            return rows
        # tabel HTML
        from .excel import _REALISASI_ALIASES, _extract, _find_mapping

        for tabel in re.finditer(r"<table[^>]*>(.*?)</table>", resp.text or "", re.S | re.I):
            baris_html = re.findall(r"<tr[^>]*>(.*?)</tr>", tabel.group(1), re.S | re.I)
            grid = [[re.sub(r"<[^>]+>", "", c).replace("&nbsp;", " ").strip()
                     for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", b, re.S | re.I)]
                    for b in baris_html]
            grid = [g for g in grid if g]
            if len(grid) < 2:
                continue
            mapping, header_idx = _find_mapping(grid, _REALISASI_ALIASES)
            if not mapping or "nop" not in mapping:
                continue
            for b in grid[header_idx + 1:]:
                d = _extract(b, mapping, "realisasi")
                if d:
                    rows.append(d)
        return rows


def _sub(text: str, mapping: dict) -> str:
    out = str(text)
    for k, v in mapping.items():
        out = out.replace("{" + k + "}", str(v))
    return out


def _as_int(v, default=0) -> int:
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return default
