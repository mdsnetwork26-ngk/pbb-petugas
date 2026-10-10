@echo off
chcp 65001 >nul
title Loket PBB - Mengisi Data Contoh
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo [!] Jalankan "Siap.bat" dulu.
  pause
  exit /b 1
)
echo Membuat 7 petugas blok, akun login, ~140 NOP ketetapan, sebagian pembayaran
echo dan file Excel contoh di folder contoh-file\ ...
".venv\Scripts\python" app.py --seed-demo
echo.
echo Selesai. Sekarang jalankan "Jalankan Aplikasi.bat" lalu login admin/admin123.
echo Untuk menghapus data contoh nanti: menu Pengaturan - bagian "Buang data contoh".
pause
