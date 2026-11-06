"""Bakanlık sitesindeki birim fiyat listelerini takip eder ve yenilerini yükler.

Akış:
  1. Başlangıç sayfalarını ve onlardan bağlantı verilen birim fiyat sayfalarını tarar.
  2. PDF / Excel / ZIP bağlantılarını bulur; bağlantı metninden yıl, dönem,
     liste (inşaat, tesisat, elektrik) ve içerik (fiyat, analiz) çıkarır.
  3. Daha önce işlenmemiş dosyaları indirir, okur, kontrol eder.
  4. Kontrolden geçenleri veri/csv klasörüne yazar ve veritabanına yükler.
     Şüpheli dosyalar yüklenmez; mevcut veriler hiçbir durumda silinmez.

Bakanlık sitesi yalnızca Türkiye'den erişilebilir; bu modül kullanıcının
bilgisayarında, platform her açıldığında arka planda çalışır.

Elle çalıştırmak için: python -m app.takip
"""
import html
import json
import re
import ssl
import threading
import traceback
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import importer, kur
from .db import AYLAR, baglan, katla

KOK = Path(__file__).resolve().parent.parent
AYAR_DOSYASI = KOK / "veri" / "takip_ayar.json"
DURUM_DOSYASI = KOK / "veri" / "takip_durum.json"
INDIRME_KLASORU = KOK / "veri" / "indirilen"
ARA_SERTIFIKA = Path(__file__).resolve().parent / "sertifika" / "ssl2buy_ara_sertifika.pem"

VARSAYILAN_AYAR = {
    # Taramanın başladığı sayfalar
    "sayfalar": ["https://yfk.csb.gov.tr/"],
    # Sadece bu alan adlarındaki sayfalar ve dosyalar izlenir
    "izinli_alanlar": ["csb.gov.tr"],
    # Bu kelimeleri içeren sayfa bağlantıları da taranır (alt sayfalar)
    "alt_sayfa_kelimeleri": ["birim fiyat", "birim-fiyat", "analiz", "guncel fiyat", "guncel-fiyat"],
    "en_fazla_sayfa": 40,
    "en_eski_yil": 2025,
}

DOSYA_UZANTILARI = (".pdf", ".xlsx", ".xls", ".zip")
HARIC_KELIMELER = ("ingilizce", "english", "rusca", "russian", "yaklasik maliyet", "yaklasik birim maliyet",
                   "duzeltme", "eklenen", "cikarilan", "degisiklik", "kilavuz", "tebligi", "yonetmelik",
                   "hizmet bedeli", "mimarlik", "muhendislik")


def _kalip(kelime: str) -> str:
    """Bakanlık dosya adlarında Türkçe harfler tireye dönüşebiliyor ("İnşaat" -> "in-aat").
    Bu yüzden her Türkçe harf için "o harf ya da boşluk" kabul eden bir kalıp üretir."""
    parca = []
    for ch in kelime:
        k = katla(ch)
        # harf kendisi, boşluk ya da hiç ("İnşaat" -> "n-aat" de görüldü)
        parca.append(f"(?:{re.escape(k)}| )?" if ch in "çğıöşüÇĞİÖŞÜ" else re.escape(k))
    return "".join(parca)


# Sıra önemli: "Elektrik Tesisatı" elektrik, "İnşaat ve Tesisat" inşaat listesidir
LISTE_KALIPLARI = [
    ("elektrik", re.compile(r"\b(?:elektrik|elk)")),
    ("tesisat", re.compile(r"\b(?:mekanik|mek)\b|\bmekanik")),
    ("insaat", re.compile(rf"\b(?:{_kalip('İnşaat')}|ins)\b|\b{_kalip('İnşaat')}")),
    ("tesisat", re.compile(rf"\b{_kalip('tesisat')}")),
]
AY_KALIPLARI = [(ay, re.compile(rf"\b{_kalip(ay)}\b")) for ay in AYLAR]

EN_AZ_POZ = 100
EN_FAZLA_FIYATSIZ_ORAN = 0.05
ZAMAN_ASIMI = 90  # saniye


# ---------------------------------------------------------------- Ayarlar ve durum

def ayar_oku() -> dict:
    ayar = dict(VARSAYILAN_AYAR)
    try:
        ayar.update(json.loads(AYAR_DOSYASI.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass
    return ayar


def durum_oku() -> dict:
    try:
        return json.loads(DURUM_DOSYASI.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"dosyalar": {}}


def _durum_yaz(durum: dict) -> None:
    DURUM_DOSYASI.parent.mkdir(parents=True, exist_ok=True)
    gecici = DURUM_DOSYASI.with_suffix(".tmp")
    gecici.write_text(json.dumps(durum, ensure_ascii=False, indent=1), encoding="utf-8")
    gecici.replace(DURUM_DOSYASI)


# ---------------------------------------------------------------- İndirme

def _ssl_baglami() -> ssl.SSLContext:
    """Sistem sertifikaları + Bakanlık sunucusunun göndermediği ara sertifika.

    webdosya.csb.gov.tr ara sertifikayı göndermiyor; tarayıcılar bunu kendileri
    tamamlar ama Python tamamlamaz. Doğrulama kapatılmaz, eksik halka eklenir.
    """
    ctx = ssl.create_default_context()
    if ARA_SERTIFIKA.exists():
        ctx.load_verify_locations(cafile=str(ARA_SERTIFIKA))
    return ctx


def _istek(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "BirimFiyatPlatformu/1.0 (kurum ici fiyat takibi)"})
    with urllib.request.urlopen(req, timeout=ZAMAN_ASIMI, context=_ssl_baglami()) as r:
        return r.read()


# ---------------------------------------------------------------- Bağlantı bulma ve sınıflandırma

@dataclass
class Baglanti:
    url: str
    metin: str          # bağlantı metni + çevresi
    sayfa: str          # bulunduğu sayfa


_A_RE = re.compile(r"<a\b[^>]*?href\s*=\s*[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", re.I | re.S)
_ETIKET_RE = re.compile(r"<[^>]+>")


def baglantilari_bul(html_metni: str, sayfa_url: str) -> list[Baglanti]:
    sonuc = []
    for m in _A_RE.finditer(html_metni):
        href = html.unescape(m.group(1)).strip()
        if href.startswith(("javascript:", "mailto:", "#")):
            continue
        metin = re.sub(r"\s+", " ", html.unescape(_ETIKET_RE.sub(" ", m.group(2)))).strip()
        # bağlantı metni kısa ise ("İndir", "Tıklayınız") önceki metinden bağlam al
        if len(metin) < 15:
            onceki = _ETIKET_RE.sub(" ", html_metni[max(0, m.start() - 300):m.start()])
            metin = re.sub(r"\s+", " ", html.unescape(onceki)).strip()[-150:] + " " + metin
        sonuc.append(Baglanti(urllib.parse.urljoin(sayfa_url, href), metin, sayfa_url))
    return sonuc


def _izinli(url: str, ayar: dict) -> bool:
    host = urllib.parse.urlparse(url).hostname or ""
    return any(host == a or host.endswith("." + a) for a in ayar["izinli_alanlar"])


def _dosya_mi(url: str) -> bool:
    return urllib.parse.urlparse(url).path.lower().endswith(DOSYA_UZANTILARI)


def siniflandir(metin: str, url: str = "", ayar: dict | None = None) -> dict | None:
    """Bağlantının hangi liste olduğunu bulur; birim fiyat listesi değilse None.

    Örnek: "2026-kas-m-elk-bf" -> 2026 Kasım elektrik birim fiyat,
           "in-aat-analiz-1"   -> inşaat analizleri, 1. kitap
    """
    ayar = ayar or VARSAYILAN_AYAR
    dosya_adi = urllib.parse.unquote(urllib.parse.urlparse(url).path.rsplit("/", 1)[-1])
    dosya_adi = re.sub(r"\.(pdf|xlsx?|zip)$", "", dosya_adi, flags=re.I)
    k = katla(f"{metin} {dosya_adi}").replace("_", " ").replace("-", " ")
    k = re.sub(r"\s+", " ", k)
    if any(h in k for h in HARIC_KELIMELER):
        return None
    analiz = "analiz" in k
    if not (analiz or "birim fiyat" in k or "birimfiyat" in k or "fiyat listesi" in k or re.search(r"\bbf\b", k)):
        return None
    liste = next((l for l, kalip in LISTE_KALIPLARI if kalip.search(k)), None)
    yillar = [int(y) for y in re.findall(r"(?<!\d)(20[2-9]\d)(?!\d)", k)]
    if not yillar:  # yıl yalnızca yükleme zaman damgasında olabilir: "...-20260115162935"
        yillar = [int(y) for y in re.findall(r"(?<!\d)(20[2-9]\d)[01]\d[0-3]\d\d{6}(?!\d)", k)][:1]
    if not liste or not yillar:
        return None
    yil = max(yillar)
    if yil < ayar["en_eski_yil"]:
        return None
    donem = next((ay for ay, kalip in AY_KALIPLARI if kalip.search(k)), "")
    sonuc = {"kurum": "CSB", "yil": yil, "donem": donem, "liste": liste, "icerik": "analiz" if analiz else "fiyat"}
    if analiz:  # analiz kitapları parçalara bölünebiliyor: "analiz-1", "Analizler 2. Kitap"
        m = re.search(r"analiz\w*\s+(\d{1,2})\b(?!\d)", k) or re.search(r"\b(\d{1,2})\s*\.?\s*(?:kitap|cilt|kisim)", k)
        sonuc["parca"] = int(m.group(1)) if m else 0
    return sonuc


def tara(ayar: dict, istek=_istek, gunluk=print) -> tuple[list[tuple[Baglanti, dict]], int]:
    """Sayfaları tarar; sınıflandırılabilen dosya bağlantılarını ve açılabilen sayfa sayısını döner."""
    kuyruk = list(ayar["sayfalar"])
    gorulen, dosyalar, acilan = set(), {}, 0
    while kuyruk and len(gorulen) < ayar["en_fazla_sayfa"]:
        url = kuyruk.pop(0)
        if url in gorulen:
            continue
        gorulen.add(url)
        try:
            icerik = istek(url).decode("utf-8", errors="replace")
        except Exception as e:  # bir sayfanın açılmaması taramayı durdurmasın
            gunluk(f"Sayfa açılamadı: {url} ({e})")
            continue
        acilan += 1
        for b in baglantilari_bul(icerik, url):
            if not _izinli(b.url, ayar):
                continue
            if _dosya_mi(b.url):
                s = siniflandir(b.metin, b.url, ayar)
                if s and b.url not in dosyalar:
                    dosyalar[b.url] = (b, s)
            elif b.url not in gorulen and any(w in katla(f"{b.metin} {b.url}") for w in ayar["alt_sayfa_kelimeleri"]):
                kuyruk.append(b.url)
    return list(dosyalar.values()), acilan


# ---------------------------------------------------------------- İşleme

def _okunacak_dosyalar(yol: Path) -> list[Path]:
    """ZIP ise içindeki PDF/Excel dosyalarını çıkarır."""
    if yol.suffix.lower() != ".zip":
        return [yol]
    hedef = yol.with_suffix("")
    hedef.mkdir(exist_ok=True)
    cikan = []
    with zipfile.ZipFile(yol) as z:
        for ad in z.namelist():
            temiz = Path(ad).name
            if temiz and temiz.lower().endswith((".pdf", ".xlsx", ".xls")):
                (hedef / temiz).write_bytes(z.read(ad))
                cikan.append(hedef / temiz)
    return cikan


def dosyayi_isle(con, yol: Path, s: dict, csv_klasoru: Path) -> str:
    """Dosyayı okur, kontrol eder, CSV'ye yazar ve yükler. Sonuç mesajı döner; sorun varsa hata fırlatır."""
    if s["icerik"] == "analiz":
        analizler = importer.analiz_oku(yol)  # okuyucu yoksa NotImplementedError
        if len(analizler) < EN_AZ_POZ:
            raise ValueError(f"Sadece {len(analizler)} analiz okunabildi, dosya biçimi farklı olabilir")
        hedef = csv_klasoru / kur.dosya_adi(s["kurum"], s["yil"], s["donem"], s["liste"], "analiz", s.get("parca", 0))
        kur.analiz_csv_yaz(hedef, analizler)
    else:
        satirlar = importer.oku(yol)
        fiyatsiz = sum(x.fiyat is None for x in satirlar)
        if len(satirlar) < EN_AZ_POZ:
            raise ValueError(f"Sadece {len(satirlar)} poz okunabildi, dosya biçimi farklı olabilir")
        if fiyatsiz > len(satirlar) * EN_FAZLA_FIYATSIZ_ORAN:
            raise ValueError(f"{len(satirlar)} pozun {fiyatsiz} tanesinin fiyatı okunamadı")
        hedef = csv_klasoru / kur.dosya_adi(s["kurum"], s["yil"], s["donem"], s["liste"])
        kur.fiyat_csv_yaz(hedef, satirlar)
    n = kur.dosya_yukle(con, hedef)
    return f"{hedef.name}: {n} kayıt"


def calistir(con=None, ayar: dict | None = None, istek=_istek, gunluk=print,
             csv_klasoru: Path | None = None, indirme_klasoru: Path | None = None) -> dict:
    """Bir takip turu. Özet döner: {'yeni': [...], 'hatalar': [...], 'bulunan': n}"""
    con = con or baglan()
    ayar = ayar or ayar_oku()
    csv_klasoru = csv_klasoru or kur.CSV_KLASORU
    indirme_klasoru = indirme_klasoru or INDIRME_KLASORU
    csv_klasoru.mkdir(parents=True, exist_ok=True)
    indirme_klasoru.mkdir(parents=True, exist_ok=True)
    durum = durum_oku()
    kayit = durum.setdefault("dosyalar", {})
    ozet = {"yeni": [], "hatalar": [], "bulunan": 0}

    bulunan, acilan = tara(ayar, istek, gunluk)
    ozet["bulunan"] = len(bulunan)
    if not acilan:
        ozet["hatalar"].append("Bakanlık sitesine ulaşılamadı (internet bağlantısını kontrol edin)")
    elif not bulunan:
        ozet["hatalar"].append("Bakanlık sayfasında birim fiyat dosyası bulunamadı; sayfa yapısı değişmiş olabilir")

    for b, s in bulunan:
        onceki = kayit.get(b.url, {})
        if onceki.get("durum") in ("yuklendi", "reddedildi"):
            continue  # işlenmiş dosya; reddedilen dosya aynı kalacağı için tekrar denenmez
        ad = re.sub(r"[^\w.\-]", "_", urllib.parse.unquote(b.url.rsplit("/", 1)[-1]))[:120]
        yol = indirme_klasoru / ad
        try:
            if not yol.exists():
                gunluk(f"İndiriliyor: {b.metin[:80]}")
                yol.write_bytes(istek(b.url))
            mesajlar = []
            for parca in _okunacak_dosyalar(yol):
                ps = s if parca == yol else (siniflandir(f"{b.metin} {parca.name}", parca.name) or s)
                mesajlar.append(dosyayi_isle(con, parca, ps, csv_klasoru))
            kayit[b.url] = {"durum": "yuklendi", "mesaj": "; ".join(mesajlar), "metin": b.metin[:200],
                            "tarih": datetime.now().isoformat(timespec="seconds"), **s}
            ozet["yeni"].extend(mesajlar)
        except NotImplementedError as e:
            # okuyucusu henüz olmayan dosya: indirildi, platform güncellenince yeniden denenir
            kayit[b.url] = {"durum": "bekliyor", "mesaj": str(e), "metin": b.metin[:200], **s}
        except ValueError as e:
            kayit[b.url] = {"durum": "reddedildi", "mesaj": str(e), "metin": b.metin[:200], **s}
            ozet["hatalar"].append(f"{b.metin[:80]}: {e}")
        except Exception as e:  # ağ hatası vb.: sonraki açılışta tekrar denenir
            kayit[b.url] = {"durum": "hata", "mesaj": str(e), "metin": b.metin[:200], **s}
            ozet["hatalar"].append(f"{b.metin[:80]}: {e}")
            gunluk(traceback.format_exc())

    durum["son_kontrol"] = datetime.now().isoformat(timespec="seconds")
    durum["son_ozet"] = ozet
    _durum_yaz(durum)
    return ozet


# ---------------------------------------------------------------- Arka planda çalıştırma

_kilit = threading.Lock()
calisiyor = False


def arka_planda_baslat() -> bool:
    """Takibi ayrı bir iş parçacığında başlatır. Zaten çalışıyorsa False döner."""
    global calisiyor
    if not _kilit.acquire(blocking=False):
        return False
    calisiyor = True

    def is_():
        global calisiyor
        try:
            calistir(baglan())  # iş parçacığının kendi veritabanı bağlantısı
        except Exception as e:
            d = durum_oku()
            d["son_kontrol"] = datetime.now().isoformat(timespec="seconds")
            d["son_ozet"] = {"yeni": [], "hatalar": [f"Kontrol yapılamadı: {e}"], "bulunan": 0}
            _durum_yaz(d)
        finally:
            calisiyor = False
            _kilit.release()

    threading.Thread(target=is_, daemon=True, name="takip").start()
    return True


if __name__ == "__main__":
    print(json.dumps(calistir(), ensure_ascii=False, indent=1))
