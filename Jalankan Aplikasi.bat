@echo off
chcp 65001 >nul
title Loket PBB - Aplikasi Berjalan
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo [!] Aplikasi belum disiapkan. Jalankan "Siap.bat" sekali dulu.
  pause
  exit /b 1
)

for /f "delims=" %%i in ('powershell -NoProfile -Command "(Get-NetIPAddress -AddressFamily IPv4 | Where-Object {$_.IPAddress -notlike '169.*' -and $_.IPAddress -ne '127.0.0.1'} | Select-Object -First 1).IPAddress"') do set IPKU=%%i

echo.
echo  ==================================================================
echo    APLIKASI LOKET PBB-P2 - JANGAN TUTUP JENDELA INI
echo  ==================================================================
echo.
echo    Layar ini = mesin aplikasi. Selama jendelanya terbuka,
echo    aplikasi jalan. Untuk berhenti: tutup jendela ini (atau Ctrl+C).
echo.
echo    Buka di komputer ini   : http://localhost:8088
if defined IPKU echo    Untuk petugas lewat WiFi : http://%IPKU%:8088
echo.
echo    User awal : admin  /  password : admin123   (segera diganti)
echo    Data tersimpan di folder data\pbb.db  - cadangan otomatis tiap hari di data\backup
echo.
".venv\Scripts\python" app.py
pause
