# Birim Fiyat Platformu (Faz 1)

Kurum içi poz arama ve basit keşif/yaklaşık maliyet uygulaması. Veriler kurumların
yayımladığı resmi birim fiyat dosyalarından (Excel, CSV veya PDF) içe aktarılır.

## Özellikler
- Poz no veya tanım ile anlık arama (Türkçe karakter duyarsız: "alci" = "alçı")
- Kurum / yıl / dönem seçimi, birim fiyat - rayiç filtresi
- Poz detayında yıllar arası fiyat ve yüzde değişim
- Keşif listesi: miktar girişi, toplam, KDV, Excel'e aktarma, yazdırma

## Kurulum
```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```
veya Docker ile:
```bash
docker build -t birimfiyat .
docker run -d -p 8000:8000 -v birimfiyat-veri:/data birimfiyat
```

## Veri yükleme
Bakanlığın (ÇŞİDB Yüksek Fen Kurulu) yayımladığı birim fiyat dosyasını indirip:
```bash
python -m app.importer "2026 Birim Fiyatlar.xlsx" --kurum CSB --yil 2026
python -m app.importer "2026 Eylul Guncel.pdf"    --kurum CSB --yil 2026 --donem Eylül
```
Docker'da: `docker exec -it <konteyner> python -m app.importer /data/dosya.xlsx --kurum CSB --yil 2026`

- Aynı kurum + yıl + dönem tekrar yüklenirse eski kayıt silinip yenisi yazılır.
- Ayrıştırıcı başlık satırını (Poz No / Tanımı / Birimi / Birim Fiyatı) kendisi bulur;
  başlık yoksa satırları kalıplarla tanır. PDF desteği metin tabanlı PDF'ler içindir
  (taranmış görüntü PDF'leri okunamaz).
- CSB dosyalarında `10.` ile başlayan pozlar rayiç olarak işaretlenir.

## Testler
```bash
python -m pytest -q
```
