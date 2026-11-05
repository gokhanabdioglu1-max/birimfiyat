# Platformun en yeni surumunu GitHub'dan indirip bu klasore kopyalar.
# Veriler (veri klasoru) ve Python ortami (.venv) korunur. Internet yoksa sessizce atlanir.
$ErrorActionPreference = 'Stop'
$adres = 'https://github.com/gokhanabdioglu1-max/birimfiyat/archive/refs/heads/main.zip'
$klasor = Split-Path -Parent $MyInvocation.MyCommand.Path
$zip = Join-Path $env:TEMP 'birimfiyat_guncel.zip'
$acik = Join-Path $env:TEMP 'birimfiyat_guncel'
try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -UseBasicParsing -Uri $adres -OutFile $zip -TimeoutSec 60
    if (Test-Path $acik) { Remove-Item $acik -Recurse -Force }
    Expand-Archive -Path $zip -DestinationPath $acik -Force
    $kaynak = (Get-ChildItem $acik | Select-Object -First 1).FullName
    # Calisan baslat.bat'in uzerine yazilmaz; degistiyse bir sonraki acilista degistirilir
    $yeniBat = Join-Path $kaynak 'baslat.bat'
    $eskiBat = Join-Path $klasor 'baslat.bat'
    if ((Test-Path $yeniBat) -and ((Get-FileHash $yeniBat).Hash -ne (Get-FileHash $eskiBat).Hash)) {
        Copy-Item $yeniBat (Join-Path $klasor 'baslat.yeni.bat') -Force
    }
    Get-ChildItem $kaynak -Force | Where-Object { $_.Name -ne 'baslat.bat' } |
        ForEach-Object { Copy-Item $_.FullName -Destination $klasor -Recurse -Force }
    Write-Host '  Platform guncel.'
} catch {
    Write-Host '  Guncelleme kontrol edilemedi, mevcut surumle devam ediliyor.'
} finally {
    Remove-Item $zip -Force -ErrorAction SilentlyContinue
    Remove-Item $acik -Recurse -Force -ErrorAction SilentlyContinue
}
