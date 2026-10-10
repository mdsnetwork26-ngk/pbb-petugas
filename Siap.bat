@echo off
chcp 65001 >nul
title Loket PBB - Persiapan Pertama Kali
cd /d "%~dp0"

echo.
echo ==================================================================
echo   Aplikasi Loket Pembayaran PBB-P2 - persiapan pertama kali
echo ==================================================================
echo.
where python >nul 2>nul
if errorlevel 1 (
  echo [!] Python belum terpasang.
  echo     Unduh dulu dari https://www.python.org/downloads/  (Python 3.11 ke atas)
  echo     SAAT INSTALL: centang "Add python.exe to PATH", lalu jalankan lagi file ini.
  pause
  exit /b 1
)

if not exist ".venv" (
  echo [1/3] Membuat lingkungan virtual .venv ...
  python -m venv .venv
)
echo [2/3] Menginstal modul yang diperlukan (Flask, openpyxl, reportlab) ...
".venv\Scripts\python" -m pip install --upgrade pip >nul
".venv\Scripts\python" -m pip install -r requirements.txt
if errorlevel 1 (
  echo [!] Gagal instal. Cek koneksi internet, atau jalankan manual:
  echo     .venv\Scripts\pip install -r requirements.txt
  pause
  exit /b 1
)
echo [3/3] Selesai. Sekarang jalankan "Jalankan Aplikasi.bat".
echo.
pause
