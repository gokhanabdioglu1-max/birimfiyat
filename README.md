# Birim Fiyat Platformu (Faz 1)

Kurum içi poz arama ve basit keşif/yaklaşık maliyet uygulaması. Veriler kurumların
yayımladığı resmi birim fiyat dosyalarından (Excel, CSV veya PDF) içe aktarılır.

## Özellikler
- Poz no veya tanım ile anlık arama (Türkçe karakter duyarsız: "alci" = "alçı")
- Kurum / yıl / dönem seçimi, birim fiyat - rayiç filtresi
- Poz detayında yıllar arası fiyat ve yüzde değişim
- Keşif listesi: miktar girişi, toplam, KDV, Excel'e aktarma, yazdırma

## Windows'ta kullanım (en kolay yol)
1. Bilgisayarınızda Python yoksa https://www.python.org/downloads/ adresinden kurun.
   Kurulumun ilk ekranında **"Add python.exe to PATH"** kutusunu işaretleyin.
2. Bu sayfanın üstündeki yeşil **Code** düğmesine basıp **Download ZIP** ile indirin ve zip'i bir klasöre çıkarın.
3. Klasördeki **baslat.bat** dosyasına çift tıklayın. İlk açılışta gerekli paketler kurulur (birkaç dakika).
4. Tarayıcıda platform kendiliğinden açılır: http://localhost:8000
   Kapatmak için siyah pencereyi kapatın. Sonraki açılışlar birkaç saniye sürer.

## Kurulum (sunucu / geliştirici)
```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```
veya Docker ile:
```bash
docker build -t birimfiyat .
docker run -d -p 8000:8000 -v birimfiyat-veri:/data birimfiyat
```

## Otomatik güncelleme
`baslat.bat` her açılışta iki şeyi kendiliğinden yapar:

1. **Platformu günceller:** GitHub'daki en yeni sürümü indirir (repo herkese açık olmalı).
   Veriler ve kurulum korunur. İnternet yoksa mevcut sürümle açılır.
2. **Bakanlık sitesini kontrol eder:** Platform açıldıktan sonra arka planda
   yfk.csb.gov.tr taranır. Yeni inşaat, tesisat veya elektrik listesi (yıl başı ya da
   aylık güncel fiyat) ya da analiz kitabı varsa indirilir, kontrol edilir ve yüklenir.
   Ekranın üstünde son kontrol zamanı ve sonucu görünür; **Güncellemeleri kontrol et**
   düğmesiyle elle de başlatılabilir.

Kontroller: 100'den az poz okunan ya da pozlarının %5'inden fazlasının fiyatı okunamayan
dosyalar yüklenmez, ekranda uyarı çıkar. Mevcut veriler hiçbir durumda silinmez.
Takip ayarları (taranacak sayfalar vb.) `veri/takip_ayar.json` ile değiştirilebilir.
Elle çalıştırmak için: `python -m app.takip`

## Veriler
`veri/csv/` klasöründeki listeler veritabanına kendiliğinden yüklenir. Veritabanı
(`veri/birimfiyat.db`) sadece bir önbellektir, silinirse bu klasörden yeniden kurulur.

Dosya adı biçimi: `KURUM_YIL_DONEM_LISTE.csv`, analizler için `..._analiz.csv`
(DONEM: `Yilbasi`, `Ocak` … `Aralik`; LISTE: `insaat`, `tesisat`, `elektrik`).

Kaynak: Çevre, Şehircilik ve İklim Değişikliği Bakanlığı Yüksek Fen Kurulu (webdosya.csb.gov.tr).
Her liste PDF'ten okunduktan sonra her pozun fiyatı (tesisat listelerinde montaj bedeliyle birlikte)
PDF metniyle tek tek karşılaştırılarak doğrulanmıştır.

| Liste | Dönemler (`veri/csv`, repoda) | Poz |
|---|---|---|
| İnşaat | 2025 Eylül; 2026 Şubat–Kasım (aylık) | 1.818 / 1.878 |
| Mekanik tesisat | 2025 Eylül; 2026 Şubat, Mart, Haziran, Temmuz, Eylül | 5.638 / 5.680 |
| Elektrik tesisat | 2026 Kasım | 5.911 |

Aylık güncel listeler Eylül 2025'te başlamıştır; daha önceki aylarda yıl başı fiyatları geçerlidir.

**`veri/ozel` (repoda yok):** Yıl başı birim fiyat kitapları (2025, 2026; inşaat, mekanik, elektrik,
rayiçler) ve analiz kitapları. Bu kitaplarda Bakanlığın "Tüm hakları saklıdır" uyarısı bulunduğu için
herkese açık repoya konmaz; kurumun kendi bilgisayarında `veri/ozel` klasöründe durur. Platform bu
klasörü de okur. Otomatik takip, eksik kitapları (diğer analiz ciltleri gibi) Bakanlık sitesinden
bulup bilgisayarınızda bu şekilde işler.

Tesisat listelerinde iki fiyat vardır: **Birim Fiyat** (montaj dahil) ve **Montaj Bedeli**
(birim fiyatın içindeki montaj payı). Platform ikisini de saklar ve gösterir.

## Veri yükleme
Bakanlığın (ÇŞİDB Yüksek Fen Kurulu) yayımladığı birim fiyat dosyasını indirip:
```bash
python -m app.importer "2026 Birim Fiyatlar.xlsx" --kurum CSB --yil 2026 --liste insaat
python -m app.importer "2026 Eylul Elektrik.pdf"  --kurum CSB --yil 2026 --donem Eylül --liste elektrik
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
