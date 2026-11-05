@echo off
chcp 65001 >nul
title Birim Fiyat Platformu
cd /d "%~dp0"

rem Python var mi?
set PY=
where py >nul 2>nul && set PY=py
if not defined PY where python >nul 2>nul && set PY=python
if not defined PY (
  echo.
  echo  Python bulunamadi.
  echo  https://www.python.org/downloads/ adresinden Python'u kurun.
  echo  Kurulumda "Add python.exe to PATH" kutusunu isaretlemeyi unutmayin.
  echo.
  start https://www.python.org/downloads/
  pause
  exit /b 1
)

rem Ilk calistirmada gerekli paketleri kur
if not exist ".venv\Scripts\python.exe" (
  echo  Ilk kurulum yapiliyor, birkac dakika surebilir...
  %PY% -m venv .venv || (echo Kurulum basarisiz. & pause & exit /b 1)
  ".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
  ".venv\Scripts\python.exe" -m pip install --quiet -r requirements.txt || (echo Paketler kurulamadi. Internet baglantinizi kontrol edin. & pause & exit /b 1)
)

echo.
echo  Birim Fiyat Platformu calisiyor: http://localhost:8000
echo  Kapatmak icin bu pencereyi kapatin.
echo.
start "" http://localhost:8000
".venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
pause