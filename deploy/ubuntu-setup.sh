#!/usr/bin/env bash
# Persiapan aplikasi Loket PBB-P2 di server Ubuntu/Debian.
# Jalankan DARI DALAM folder proyek ini:  sudo bash deploy/ubuntu-setup.sh
# Aman dijalankan ulang (idempoten): tidak menimpa database dan tidak menghapus apa pun.
set -euo pipefail

cd "$(dirname "$0")/.."
APPS="/opt/pbb-petugas"
USER_RUN="pbb"
SERVICE="pbb-petugas"
PORT="${PBB_PORT:-8088}"

echo
echo "=== Aplikasi Loket PBB-P2 - persiapan Ubuntu ==="
echo "    folder proyek : $(pwd)"
echo "    akan dipasang : ${APPS}  (dijalankan sebagai user ${USER_RUN}, port ${PORT})"
echo

if [ "$(id -u)" != "0" ]; then
  echo "[!] Jalankan sebagai root:  sudo bash deploy/ubuntu-setup.sh"
  exit 1
fi

# 1. dependensi sistem ------------------------------------------------------------
if ! command -v python3 >/dev/null 2>&1; then
  echo "[1/6] memasang python3 + venv ..."
  apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3 python3-venv python3-pip
else
  echo "[1/6] python3 sudah ada: $(python3 -V)"
  if ! python3 -c "import ensurepip" >/dev/null 2>&1; then
    apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3-venv
  fi
fi

# 1b. pastikan Python >=3.9 (Flask 3.1.3 & reportlab 5.0.1 menuntutnya) ----------
PYRUN=${PYTHON:-python3}
if ! "$PYRUN" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1; then
  echo "[1b] Python di server ini $($PYRUN -V 2>&1) - butuh >=3.9, mencoba memasang python3.10 ..."
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq software-properties-common >/dev/null 2>&1 || true
  add-apt-repository -y ppa:deadsnakes/ppa >/dev/null 2>&1 || true
  apt-get update -qq >/dev/null 2>&1 || true
  if DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3.10 python3.10-venv >/dev/null 2>&1; then
    PYRUN=python3.10
    echo "     memakai python3.10"
  else
    echo "     [!] gagal memasang python3.10. Solusi manual ada di doc/DEPLOY-UBUNTU.md (bagian troubleshooting)."
    exit 1
  fi
else
  echo "[1b] versi Python mencukupi: $("$PYRUN" -V)"
fi

# 2. user khusus (tanpa shell login) ---------------------------------------------
if id "${USER_RUN}" >/dev/null 2>&1; then
  echo "[2/6] user ${USER_RUN} sudah ada"
else
  echo "[2/6] membuat user layanan ${USER_RUN} (nologin) ..."
  adduser --system --group --no-create-home --shell /usr/sbin/nologin "${USER_RUN}" || \
    useradd --system --user-group --shell /usr/sbin/nologin "${USER_RUN}"
fi

# 3. salin kode ke /opt -----------------------------------------------------------
echo "[3/6] menyalin berkas aplikasi ke ${APPS} ..."
SRC="$(pwd)"
mkdir -p "${APPS}"
# pindahkan DULU berkas yang masih dipakai, baru salin: jangan pernah hapus .venv & data
tar -C "${SRC}" -cf - --exclude .venv --exclude data --exclude __pycache__ --exclude .pytest_cache . \
  | tar -C "${APPS}" -xf -
mkdir -p "${APPS}/data" "${APPS}/data/backup" "${APPS}/data/upload" "${APPS}/data/auto/sudah-diproses" "${APPS}/data/cetak"

# 4. virtualenv + modul -----------------------------------------------------------
if [ ! -x "${APPS}/.venv/bin/python" ]; then
  echo "[4/6] membuat .venv dan instal Flask/openpyxl/reportlab/waitress ..."
  "$PYRUN" -m venv "${APPS}/.venv"
fi
"${APPS}/.venv/bin/pip" install -q --upgrade pip
"${APPS}/.venv/bin/pip" install -q -r "${APPS}/requirements.txt"

# 5. service systemd --------------------------------------------------------------
echo "[5/6] memasang service systemd '${SERVICE}' ..."
sed -e "s#^User=.*#User=${USER_RUN}#" \
    -e "s#^Group=.*#Group=${USER_RUN}#" \
    -e "s#^WorkingDirectory=.*#WorkingDirectory=${APPS}#" \
    -e "s#^Environment=.*#Environment=PBB_PORT=${PORT}#" \
    "${APPS}/deploy/pbb-petugas.service" > /etc/systemd/system/${SERVICE}.service
chmod 644 /etc/systemd/system/${SERVICE}.service
systemctl daemon-reload
systemctl enable "${SERVICE}" >/dev/null 2>&1 || true

# 6. hak tulis ---------------------------------------------------------------------
echo "[6/6] memberi hak tulis folder data ... (port ${PORT})"
chown -R "${USER_RUN}:${USER_RUN}" "${APPS}/data"
chmod 750 "${APPS}/data"

# peringatan bila port sudah dipakai layanan lain di host ini (server sering sudah menjalankan hal lain)
if command -v ss >/dev/null 2>&1 && ss -ltn 2>/dev/null | grep -q ":${PORT} " && ! systemctl is-active --quiet "${SERVICE}"; then
  echo
  echo "[!] Perhatian: port ${PORT} sudah dipakai proses lain di server ini:"
  ss -ltnp 2>/dev/null | grep ":${PORT} " | sed 's/^/      /'
  echo "    Ganti port bila perlu:  sed -i 's/PBB_PORT=.*/PBB_PORT=8089/' /etc/systemd/system/${SERVICE}.service"
  echo "    lalu: systemctl daemon-reload && systemctl restart ${SERVICE}"
  echo
fi

systemctl restart "${SERVICE}"
sleep 2
if systemctl is-active --quiet "${SERVICE}"; then
  STATUS="AKTIF"
else
  STATUS="BERMASALAH (lihat: journalctl -u ${SERVICE} -n 40)"
fi
IP=$(hostname -I 2>/dev/null | awk '{print $1}')

cat <<EOF

==================================================================
  SELESAI - status service: ${STATUS}
==================================================================
  Uji dari server     : curl -I http://127.0.0.1:${PORT}/login
  Dibuka petugas      : http://${IP:-IP-server}:${PORT}
  Login awal          : admin / admin123   (SEGERA diganti!)

  Kalau port belum terbuka di jaringan, izinkan:
     sudo ufw allow ${PORT}/tcp comment 'loket PBB'
  (untuk akses internal saja: sudo ufw allow from 192.168.1.0/24 to any port ${PORT})

  Basis data + cadangan ada di  ${APPS}/data
  Panduan lengkap             : doc/DEPLOY-UBUNTU.md
==================================================================
EOF
