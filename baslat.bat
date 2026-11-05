@echo off
chcp 65001 >nul
title Birim Fiyat Platformu
cd /d "%~dp0"

rem Onceki guncellemeden kalan yeni baslatma dosyasi varsa onu kullan
if exist "baslat.yeni.bat" (move /y "baslat.yeni.bat" "baslat.bat" >nul & start "" "%~dp0baslat.bat" & exit /b)

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

echo.
echo  Yeni surum kontrol ediliyor...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0guncelle.ps1"

rem Ilk calistirmada Python ortamini kur
if not exist ".venv\Scripts\python.exe" (
  echo  Ilk kurulum yapiliyor, birkac dakika surebilir...
  %PY% -m venv .venv || (echo Kurulum basarisiz. & pause & exit /b 1)
  ".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
)
rem Gerekli paketler (yeni surumde eklenen olursa kurulur; internet yoksa mevcut paketlerle devam)
".venv\Scripts\python.exe" -m pip install --quiet --disable-pip-version-check -r requirements.txt || echo  Paketler kontrol edilemedi, mevcut kurulumla devam ediliyor.

echo.
echo  Birim Fiyat Platformu calisiyor: http://localhost:8000
echo  Bakanlik sitesinde yeni liste olup olmadigi arka planda kontrol ediliyor.
echo  Kapatmak icin bu pencereyi kapatin.
echo.
set BF_TAKIP=1
rem Sunucu ayaga kalkinca tarayiciyi ac
start "" /min cmd /c "timeout /t 4 /nobreak >nul & start http://localhost:8000"
".venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
pause
