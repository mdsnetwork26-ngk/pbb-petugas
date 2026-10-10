#!/usr/bin/env bash
# Jalankan aplikasi di Linux/macOS/Ubuntu.  Persiapan pertama kali: ./start.sh --siap
# Port bisa diganti:  PBB_PORT=8090 ./start.sh
# Untuk pasang sebagai layanan systemd di server: sudo bash deploy/ubuntu-setup.sh
set -e
cd "$(dirname "$0")"
APP=$(pwd)
PY=${PYTHON:-python3}
PORT=${PBB_PORT:-8088}
# Pulihkan bit eksekusi sendiri bila hilang (file sempat lewat flashdisk/Windows/smb)
if [ ! -x "$0" ] && [ -w "$0" ]; then chmod +x "$0" 2>/dev/null || true; fi
[ -f deploy/ubuntu-setup.sh ] && [ ! -x deploy/ubuntu-setup.sh ] && chmod +x deploy/ubuntu-setup.sh 2>/dev/null || true

if [ ! -f "$APP/requirements.txt" ] || [ ! -f "$APP/app.py" ]; then
  echo "[!] start.sh harus ada di dalam folder aplikasi (bersama app.py & requirements.txt)."
  echo "    Jalankan dari folder itu, mis.:  cd /opt/pbb-petugas && ./start.sh"
  echo "    atau pasang sebagai layanan:     sudo bash deploy/ubuntu-setup.sh"
  exit 1
fi

if [ "${1:-}" = "--siap" ]; then shift; fi        # --siap = pancing instalasi, jangan diteruskan

# port yang benar-benar dipakai: argumen --port lebih didahulukan daripada PBB_PORT
AKU=$PORT; ADA_PORT=0; PREV=""; ARGS=()
for a in "$@"; do
  case "$a" in
    --port=*) AKU=${a#--port=}; ARGS+=("--port" "$AKU"); ADA_PORT=1; continue ;;
  esac
  case "$PREV" in
    --port|-p) AKU=$a; ADA_PORT=1 ;;
  esac
  ARGS+=("$a"); PREV=$a
done

# jangan mulai kalau port sudah dipakai proses lain di server ini (mis. server ini sudah ada aplikasi lain)
if command -v ss >/dev/null 2>&1; then
  if ss -ltn 2>/dev/null | grep -q ":${AKU} "; then
    echo "[!] Port ${AKU} sudah dipakai proses lain di server ini."
    echo "    Proses yang memakai: ss -ltnp | grep :${AKU}"
    echo "    Pindahkan ke port lain:  ./start.sh --port 8089    atau    PBB_PORT=8089 ./start.sh"
    exit 1
  fi
fi

# aplikasi ini butuh Python >=3.9 (Flask 3.1.3 / reportlab 5.0.1 menuntutnya)
if ! "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1; then
  echo "[!] Python terlalu lama: $($PY -V 2>&1) - aplikasi ini butuh Python 3.9 ke atas."
  echo "    Ubuntu 20.04:  sudo apt-get install -y software-properties-common"
  echo "                   sudo add-apt-repository -y ppa:deadsnakes/ppa"
  echo "                   sudo apt-get update && sudo apt-get install -y python3.10 python3.10-venv"
  echo "    lalu jalankan: PYTHON=python3.10 bash start.sh --siap"
  echo "    atau tetap di 3.8 dengan paket lama: ./.venv/bin/pip install \"Flask==2.3.3\" \"reportlab==4.1.0\" \"waitress==2.1.2\" openpyxl"
  exit 1
fi

if [ ! -x "$APP/.venv/bin/python" ]; then
  echo "[1/2] membuat .venv ..."
  "$PY" -m venv "$APP/.venv"
  echo "[2/2] instal modul (Flask, openpyxl, reportlab, waitress) ..."
  "$APP/.venv/bin/pip" install -q --upgrade pip
  "$APP/.venv/bin/pip" install -q -r "$APP/requirements.txt"
fi
VENV="$APP/.venv"

if [ "${1:-}" = "--seed" ]; then exec "$VENV/bin/python" app.py --seed-demo; fi

IP=$(hostname -I 2>/dev/null | awk '{print $1}')
[ -n "$IP" ] || IP=$(ipconfig getifaddr en0 2>/dev/null || echo "IP-komputer")
echo "  http://localhost:${AKU}   (petugas via LAN/WiFi: http://${IP}:${AKU})"
if [ "$ADA_PORT" = 1 ]; then
  exec "$VENV/bin/python" app.py "${ARGS[@]}"
else
  exec "$VENV/bin/python" app.py --port "${AKU}" ${1:+"$@"}
fi
