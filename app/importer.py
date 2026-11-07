"""Birim fiyat dosyalarını (Excel, CSV, PDF) veritabanına aktarır.

Kullanım:
    python -m app.importer DOSYA --kurum CSB --yil 2026 [--donem Eylül]

Dosya biçimi kurumdan kuruma ve yıldan yıla değiştiği için ayrıştırıcı
sabit sütun sırasına güvenmez: önce başlık satırını arar, bulamazsa her
satırda poz numarası, tanım, birim ve fiyatı kalıplarla tanır.
"""
import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from .db import baglan, donem_sira, katla

POZ_RE = re.compile(r"^(?:[A-ZÇĞİÖŞÜ]{1,5}[.\-/ ]?)?\d{1,4}(?:[./\-]\d{1,5}){1,4}(?:/[A-Z0-9]{1,4})?[A-Z]?$")
SAYI_RE = re.compile(r"^-?(?:\d{1,3}(?:\.\d{3})*(?:,\d+)?|\d+(?:,\d+)?|\d+\.\d+)$")
BIRIMLER = {
    "m", "m2", "m²", "m3", "m³", "m1", "mt", "km", "cm", "mm", "kg", "gr", "ton", "t", "ad", "ad.", "adet",
    "sa", "sa.", "saat", "gün", "gun", "ay", "lt", "l", "litre", "takım", "tk", "set", "kwh", "kw", "kva",
    "dm3", "dm³", "m²/ay", "m2/ay", "m3-km", "m³-km", "ton-km", "ton.km", "sefer", "nokta", "parça", "götürü",
}


@dataclass
class Kalem:
    grup: str | None
    poz_no: str | None
    tanim: str
    birim: str | None
    miktar: float | None
    birim_fiyat: float | None
    tutar: float | None


@dataclass
class Analiz:
    poz_no: str
    tanim: str
    birim: str | None
    kalemler: list
    ara_toplam: float | None = None
    kar_orani: float | None = None
    fiyat: float | None = None


@dataclass
class Satir:
    poz_no: str
    tanim: str
    birim: str | None
    fiyat: float | None
    montaj: float | None = None   # tesisat listelerinde ayrıca verilen montaj bedeli


def sayi(deger) -> float | None:
    """'1.234,56' veya 1234.56 gibi değerleri float'a çevirir."""
    if deger is None:
        return None
    if isinstance(deger, (int, float)):
        return float(deger) if deger == deger else None  # NaN kontrolü
    s = str(deger).strip().replace("TL", "").replace("₺", "").strip()
    if not s or not SAYI_RE.match(s):
        return None
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    elif s.count(".") > 1 or re.match(r"^-?\d{1,3}\.\d{3}$", s):
        s = s.replace(".", "")
    try:
        return float(s)
    except ValueError:
        return None


def _hucre_sayi(metin: str) -> float | None:
    """Hücredeki sayı; hücreye sığmayıp iki satıra bölünmüş sayıları da birleştirir
    ("10.063.056,5 9" -> 10063056.59)."""
    v = sayi(metin)
    if v is None and metin and re.fullmatch(r"[\d.,]+(?: [\d.,]+)+", metin):
        v = sayi(metin.replace(" ", ""))
    return v


def poz_mu(deger) -> bool:
    return isinstance(deger, str) and bool(POZ_RE.match(deger.strip()))


def birim_mi(deger) -> bool:
    return isinstance(deger, str) and deger.strip().lower() in BIRIMLER


# ---------------------------------------------------------------- Excel / CSV

BASLIKLAR = {
    "poz_no": ("poz", "poz no", "poz numarası", "poz no.", "kod"),
    "tanim": ("tanım", "tanımı", "tanim", "açıklama", "imalatın cinsi", "iş kalemi", "adı", "cinsi"),
    "birim": ("birim", "birimi", "ölçü birimi"),
    "fiyat": ("birim fiyat", "birim fiyatı", "fiyat", "fiyatı", "birim fiyatı (tl)", "tutar", "rayiç"),
}


def _baslik_eslestir(hucreler: list) -> dict | None:
    eslesme = {}
    for i, h in enumerate(hucreler):
        h = katla(str(h or "")).strip()
        if not h:
            continue
        if "montaj" in h:  # "Montaj Bedeli" (tesisat listeleri)
            eslesme.setdefault("montaj", i)
        elif "fiyat" in h or h in ("tutar", "rayic"):
            eslesme.setdefault("fiyat", i)
        else:
            for alan in ("poz_no", "tanim", "birim"):
                if alan not in eslesme and any(h == katla(a) or h.startswith(katla(a) + " ") for a in BASLIKLAR[alan]):
                    eslesme[alan] = i
                    break
    return eslesme if {"poz_no", "tanim", "fiyat"} <= eslesme.keys() else None


def _satir_tahmin(hucreler: list) -> Satir | None:
    """Başlık bulunamadığında satırı kalıplarla çözer."""
    dolu = [h for h in hucreler if h is not None and str(h).strip()]
    poz = next((str(h).strip() for h in dolu if poz_mu(str(h))), None)
    if not poz:
        return None
    metinler = [str(h).strip() for h in dolu if isinstance(h, str) and not poz_mu(h) and sayi(h) is None]
    birim = next((m for m in metinler if birim_mi(m)), None)
    tanimlar = [m for m in metinler if m != birim]
    sayilar = [sayi(h) for h in dolu if sayi(h) is not None]
    if not tanimlar:
        return None
    return Satir(poz, max(tanimlar, key=len), birim, sayilar[-1] if sayilar else None)


def excel_oku(yol: Path) -> list[Satir]:
    import pandas as pd

    if yol.suffix.lower() == ".csv":
        ilk = yol.read_text(encoding="utf-8-sig", errors="replace").split("\n", 1)[0]
        ayrac = ";" if ";" in ilk else ("\t" if "\t" in ilk else ",")  # Türkçe CSV'lerde ondalık ayraç virgüldür
        tablolar = {"csv": pd.read_csv(yol, header=None, dtype=object, sep=ayrac, encoding="utf-8-sig")}
    else:
        tablolar = pd.read_excel(yol, header=None, dtype=object, sheet_name=None)

    sonuc: list[Satir] = []
    for df in tablolar.values():
        df = df.where(df.notna(), None)
        eslesme = None
        for satir in df.itertuples(index=False):
            hucreler = list(satir)
            if eslesme is None and (e := _baslik_eslestir(hucreler)):
                eslesme = e
                continue
            if eslesme:
                poz = hucreler[eslesme["poz_no"]]
                if poz is None or not poz_mu(str(poz)):
                    continue
                tanim = hucreler[eslesme["tanim"]]
                birim = hucreler[eslesme["birim"]] if "birim" in eslesme else None
                montaj = sayi(hucreler[eslesme["montaj"]]) if "montaj" in eslesme else None
                sonuc.append(Satir(str(poz).strip(), str(tanim or "").strip(),
                                   str(birim).strip() if birim else None, sayi(hucreler[eslesme["fiyat"]]), montaj))
            elif s := _satir_tahmin(hucreler):
                sonuc.append(s)
    return sonuc


# ---------------------------------------------------------------- PDF

_FIYAT2 = r"\d{1,3}(?:\.\d{3})*,\d{2}"
# "POZ tanım [birim] fiyat [montaj]"; montaj yalnızca tesisat sayfalarında ("Montaj Bedeli" sütunu)
PDF_SATIR_RE = re.compile(
    rf"^(?P<poz>\S+)\s+(?P<tanim>.+?)(?:\s+(?P<birim>[^\d\s]\S*))?\s+(?P<fiyat>{_FIYAT2})(?:\s+(?P<montaj>{_FIYAT2}))?$"
)
_OLCU_RE = re.compile(r"\(\s*Ölçü\s*:\s*([^)]+?)\.?:?\s*\)", re.I)


def pdf_oku(yol: Path) -> list[Satir]:
    """PDF birim fiyat listesini okur.

    Tablo çizgileri olan sayfalarda (Bakanlık listeleri böyle) her satır, yatay
    kenarlıklar arasındaki bant olarak okunur; böylece poz numarasının üstüne
    ve altına taşan uzun tanımlar doğru poza bağlanır. Çizgisiz sayfalarda
    satır satır metin okumaya geri dönülür.
    """
    import pdfplumber

    sonuc: list[Satir] = []
    basliklar: dict = {}  # sütun başlıkları; başlıksız sayfalar öncekini kullanır
    with pdfplumber.open(yol) as pdf:
        for sayfa in _sayfalar(pdf):
            satirlar = _pdf_tablo_sayfasi(sayfa, basliklar)
            if satirlar is None:
                satirlar = _pdf_konum_sayfasi(sayfa, basliklar)
            sonuc.extend(satirlar if satirlar is not None else _pdf_metin_sayfasi(sayfa, sonuc))
    return sonuc


def _sayfalar(pdf):
    """Sayfaları sırayla verir ve her sayfadan sonra belleği bırakır.

    pdfplumber okunan sayfaları önbellekte tutar; 700 sayfalık bir kitapta bu
    birkaç GB'a çıkıp bilgisayarı yavaşlatabiliyor.
    """
    for sayfa in pdf.pages:
        try:
            yield sayfa
        finally:
            sayfa.close()


def _kumele(degerler, tolerans):
    """Birbirine yakın koordinatları tek değerde birleştirir."""
    gruplar: list[list[float]] = []
    for d in sorted(degerler):
        if gruplar and d - gruplar[-1][-1] <= tolerans:
            gruplar[-1].append(d)
        else:
            gruplar.append([d])
    return [sum(g) / len(g) for g in gruplar]


def _pdf_tablo_sayfasi(sayfa, basliklar: dict | None = None) -> list[Satir] | None:
    """Tablo çizgileri olan bir sayfayı okur; çizgi yoksa None döner.

    Sütun sayısı ve sırası sabit varsayılmaz: her sütunun rolü (poz no, tanım,
    birim, fiyat) içeriğine bakılarak bulunur. Böylece inşaat, tesisat ve
    elektrik listelerinin farklı düzenleri aynı kodla okunur.
    """
    from collections import Counter

    dikey = [r for r in sayfa.rects if r["width"] < 2 and r["height"] > 2]
    yatay = [r for r in sayfa.rects if r["height"] < 2 and r["width"] > 2]
    if len(dikey) < 4 or len(yatay) < 5:
        return None
    sayac = Counter(round(r["x0"]) for r in dikey)
    esik = max(2, max(sayac.values()) * 0.25)  # tek tük kısa çizgileri (birleşik hücreler) yok say
    sinir = _kumele([x for x, n in sayac.items() if n >= esik], 3)
    if len(sinir) < 4:
        return None

    ys = _kumele([r["top"] for r in yatay], 3)
    kelimeler = sayfa.extract_words(keep_blank_chars=False, use_text_flow=False)
    bantlar = []
    for ust, alt in zip(ys, ys[1:]):
        hucre = [[] for _ in range(len(sinir) - 1)]
        for w in kelimeler:
            if ust <= (w["top"] + w["bottom"]) / 2 < alt:
                orta = (w["x0"] + w["x1"]) / 2
                for i in range(len(hucre)):
                    if sinir[i] - 2 <= orta < sinir[i + 1] + 2:
                        hucre[i].append(w)
                        break
        if any(hucre):
            bantlar.append([_satir_metni(h) for h in hucre])

    # İlk poz satırından önceki bantlar sütun başlıklarıdır ("Birim Fiyat", "Montaj Bedeli" ...)
    basliklar = {} if basliklar is None else basliklar
    ilk_poz = next((i for i, h in enumerate(bantlar) if any(poz_mu(x) for x in h)), len(bantlar))
    if ilk_poz:
        ust = [" ".join(h[i] for h in bantlar[:ilk_poz]) for i in range(len(sinir) - 1)]
        if any("fiyat" in katla(x) or "montaj" in katla(x) for x in ust):
            basliklar.clear()
            basliklar.update({len(ust): ust})
    roller = _sutun_rolleri(bantlar, basliklar.get(len(sinir) - 1))
    if roller is None:
        return None
    p, t, b, f, m = roller
    sonuc = []
    for h in bantlar:
        if poz_mu(h[p]):
            sonuc.append(Satir(h[p], h[t], (h[b] if b is not None else "") or None, _hucre_sayi(h[f]),
                               _hucre_sayi(h[m]) if m is not None else None))
    return sonuc


def _pdf_konum_sayfasi(sayfa, basliklar: dict) -> list[Satir] | None:
    """Sütun çizgisi olan ama satır çizgisi olmayan sayfaları okur.

    Uzun tanımlar poz satırının üstüne ve altına taşar; her tanım satırı dikeyde
    en yakın poz satırına bağlanır. Kalın yazılmış satırlar bölüm başlığıdır, atlanır.
    """
    dikey = [r for r in sayfa.rects if r["width"] < 2 and r["height"] > 50]
    sinir = _kumele([r["x0"] for r in dikey], 3)
    if len(sinir) < 4:
        return None
    kelimeler = sayfa.extract_words(extra_attrs=["fontname"])
    satirlar = []  # (y, [hücre metinleri], kalın mı)
    for y in _kumele([w["top"] for w in kelimeler], 2):
        hucre = [[] for _ in range(len(sinir) - 1)]
        kalin = True
        for w in kelimeler:
            if abs(w["top"] - y) <= 2:
                orta = (w["x0"] + w["x1"]) / 2
                for i in range(len(hucre)):
                    if sinir[i] - 2 <= orta < sinir[i + 1] + 2:
                        hucre[i].append(w)
                        kalin = kalin and "bold" in w["fontname"].lower()
                        break
        if any(hucre):
            satirlar.append((y, [_satir_metni(h) for h in hucre], kalin))
    ilk_poz = next((i for i, (_, h, _) in enumerate(satirlar) if any(poz_mu(x) for x in h)), len(satirlar))
    if ilk_poz:
        ust = [" ".join(h[i] for _, h, _ in satirlar[:ilk_poz]) for i in range(len(sinir) - 1)]
        if any("fiyat" in katla(x) or "montaj" in katla(x) for x in ust):
            basliklar.clear()
            basliklar.update({len(ust): ust})
    roller = _sutun_rolleri([h for _, h, _ in satirlar], basliklar.get(len(sinir) - 1))
    if roller is None:
        return None
    p, t, b, f, m = roller
    pozlar = [(y, h) for y, h, _ in satirlar if poz_mu(h[p])]
    if not pozlar:
        return []
    parcalar: dict[int, list] = {i: [(y, h[t])] for i, (y, h) in enumerate(pozlar) if h[t]}
    for y, h, kalin in satirlar[ilk_poz:]:
        if poz_mu(h[p]) or kalin or not h[t]:
            continue
        en_yakin = min(range(len(pozlar)), key=lambda i: abs(pozlar[i][0] - y))
        parcalar.setdefault(en_yakin, []).append((y, h[t]))
    sonuc = []
    for i, (y, h) in enumerate(pozlar):
        tanim = " ".join(x for _, x in sorted(parcalar.get(i, [])))
        sonuc.append(Satir(h[p], tanim, h[b] if b is not None and h[b] else None, _hucre_sayi(h[f]),
                           _hucre_sayi(h[m]) if m is not None else None))
    return sonuc


def _sutun_rolleri(bantlar: list[list[str]], baslik: list[str] | None = None):
    """Hangi sütunun poz no, tanım, birim, fiyat ve montaj bedeli olduğunu bulur.

    İçeriğe bakar; birden çok sayı sütunu varsa başlıktan "montaj" yazanı ayırır.
    """
    if not bantlar:
        return None
    n = len(bantlar[0])
    poz_say = [sum(poz_mu(h[i]) for h in bantlar) for i in range(n)]
    p = max(range(n), key=poz_say.__getitem__)
    if poz_say[p] == 0:
        return None
    satirlar = [h for h in bantlar if poz_mu(h[p])]
    # "Ad", "m²" gibi tam birimler 2 puan; "100 m²", "1000 Ad" gibi katlı birimler 1 puan
    # (tanımı "1000 KW" olan satırlarda tanım sütunu birim sanılmasın)
    birim_say = [sum(2 * birim_mi(h[i]) + bool(re.fullmatch(r"\d+ \S+", h[i]) and birim_mi(h[i].split()[1]))
                     for h in satirlar) if i != p else -1 for i in range(n)]
    b = max(range(n), key=birim_say.__getitem__)
    if birim_say[b] < len(satirlar) * 0.3:
        b = None
    sayisal = [i for i in range(n) if i not in (p, b) and sum(_hucre_sayi(h[i]) is not None for h in satirlar) >= len(satirlar) * 0.3]
    if not sayisal:
        return None
    m = None
    if baslik and len(sayisal) > 1:
        m = next((i for i in sayisal if "montaj" in katla(baslik[i])), None)
    fiyatlar = [i for i in sayisal if i != m]
    if not fiyatlar:
        return None
    if baslik and any("fiyat" in katla(baslik[i]) for i in fiyatlar):
        f = max(i for i in fiyatlar if "fiyat" in katla(baslik[i]))
    else:
        f = max(fiyatlar)  # başlık yoksa en sağdaki sayısal sütun
    kalan = [i for i in range(n) if i not in (p, b) and i not in sayisal]
    if not kalan:
        return None
    t = max(kalan, key=lambda i: sum(len(h[i]) for h in satirlar))
    return p, t, b, f, m


def _satir_metni(kelimeler) -> str:
    """Kelimeleri okuma sırasına (yukarıdan aşağı, soldan sağa) dizip birleştirir."""
    kelimeler = sorted(kelimeler, key=lambda w: (round(w["top"]), w["x0"]))
    return re.sub(r"\s+", " ", " ".join(w["text"] for w in kelimeler)).strip()


def _pdf_metin_sayfasi(sayfa, onceki: list[Satir] | None = None) -> list[Satir]:
    """Tablo çizgisi olmayan sayfaları satır satır okur (ör. yıl başı birim fiyat kitabı).

    - Poz satırı: "POZ tanım [birim] fiyat [montaj bedeli]"
    - Fiyatsız poz satırı bir grup başlığıdır ("35.135.3500 ... Sayaçlar: (Ölçü: Ad.)");
      altındaki açıklama atlanır, "(Ölçü: Ad.)" birimi birimsiz pozlara verilir.
    - Fiyatlı pozun altındaki satırlar tanımın devamıdır.
    - Sayfa kenarındaki dikey "Sıra No" harfleri poz hizasının solunda kalır, atlanır.
    """
    kelimeler = sayfa.extract_words()
    poz_x = [w["x0"] for w in kelimeler if re.fullmatch(r"\d{2}\.\d{3}\.\d{4}", w["text"])]
    if poz_x:
        sol = min(poz_x) - 0.5
        sayfa = sayfa.filter(lambda o: o.get("object_type") != "char" or o["x0"] >= sol)
    metin = sayfa.extract_text() or ""
    montajli = "montaj" in katla(metin)  # başlık "Montaj / Bedeli" diye bölünebiliyor
    birim_sutunu = "birimi" in katla(metin)  # inşaat: "Ölçü Birimi"; tesisat sayfalarında birim sütunu yok
    sonuc: list[Satir] = []
    son: Satir | None = None       # tanımı devam eden poz
    grup_birimi: str | None = None
    for satir in metin.splitlines():
        satir = satir.strip()
        if not satir:
            continue
        ilk = satir.split()[0]
        m = PDF_SATIR_RE.match(satir)
        if m and poz_mu(m["poz"]):
            tanim, birim, fiyat, montaj = m["tanim"], m["birim"], m["fiyat"], m["montaj"]
            if montaj and not montajli:  # montaj sütunu olmayan sayfada sondaki sayı fiyattır
                tanim = " ".join(x for x in (tanim, birim, fiyat) if x)
                birim, fiyat, montaj = None, montaj, None
                if birim_mi(tanim.rsplit(" ", 1)[-1]):
                    tanim, birim = tanim.rsplit(" ", 1)
            if birim and (not birim_mi(birim) or not birim_sutunu):  # birim sütunu yoksa son kelime tanıma aittir
                tanim, birim = f"{tanim} {birim}", None
            son = Satir(m["poz"], tanim, birim or grup_birimi, sayi(fiyat), sayi(montaj) if montaj else None)
            sonuc.append(son)
        elif poz_mu(ilk):  # fiyatsız poz satırı: grup başlığı
            o = _OLCU_RE.search(satir)
            grup_birimi = o.group(1).strip() if o else grup_birimi
            son = None
        elif son is not None and not _sayfa_ustu(satir):
            son.tanim += " " + satir
    return sonuc


def _sayfa_ustu(satir: str) -> bool:
    k = katla(satir)
    return k.startswith(("poz no", "sayfa", "birim fiyat")) or bool(re.fullmatch(r"[\d\s/\-]+", satir))


LISTE_ONEKLERI = {"25": "tesisat", "35": "elektrik"}  # ÇŞİDB: 25.xxx mekanik, 35.xxx elektrik tesisat


def listelere_bol(satirlar: list[Satir], varsayilan: str = "insaat") -> dict[str, list[Satir]]:
    """Pozları poz numarasının ilk iki hanesine göre listelere ayırır (25 -> tesisat, 35 -> elektrik).

    Diğer pozlar (15 inşaat, 10 rayiç, 19 yardımcı) varsayılan listeye gider. Aylık listelerde
    zaten tek liste vardır; yıl başı kitabında üçü birlikte olduğu için bu ayrım gerekir.
    """
    sonuc: dict[str, list[Satir]] = {}
    for x in satirlar:
        liste = LISTE_ONEKLERI.get(x.poz_no[:2], varsayilan if varsayilan in ("insaat", "tesisat", "elektrik") else "insaat")
        if varsayilan in ("tesisat", "elektrik") and x.poz_no[:2] not in LISTE_ONEKLERI:
            liste = varsayilan  # tesisat listesindeki rayiç vb. pozlar kendi listesinde kalır
        sonuc.setdefault(liste, []).append(x)
    return sonuc


# ---------------------------------------------------------------- Analiz kitapları

_SAYI = r"-?(?:\d{1,3}(?:\.\d{3})+|\d+)(?:,\d+)?"
_KALEM_RE = re.compile(rf"^(?:(?P<poz>\S+)\s+)?(?P<tanim>.*?)\s+(?P<birim>\S+)\s+(?P<miktar>{_SAYI})\s+"
                       rf"(?P<bf>{_SAYI})\s+(?P<tutar>{_SAYI})$")
# Tanımı/birimi ayrı satırlara taşmış kalem: "10.110.1002 0,75 445,00 333,75"
_KALEM_KISA_RE = re.compile(rf"^(?P<poz>\S+)\s+(?P<miktar>{_SAYI})\s+(?P<bf>{_SAYI})\s+(?P<tutar>{_SAYI})$")
# Ara toplam üzerinden oranlı kalem: "Toplam* 0,03 34,87" (%3 şablon, çalışma sehpası vb.)
_ORAN_KALEM_RE = re.compile(rf"^(?P<tanim>\D.*?)\s+(?P<oran>0,\d+)\s+(?P<tutar>{_SAYI})$")
# Toplam satırları: inşaat ("Malzeme + İşçilik Tutarı", "25 % Yüklenici kârı...", "1 m³ Fiyatı")
# ve tesisat ("Kârsız Malzeme ve Montaj (TL)", "% 25 Yüklenici Kârı ve Montaj Bedeli (TL)",
# "Genel Giderler Dahil Birim Fiyat (TL)") analizleri
_ARA_RE = re.compile(rf"^(?:Malzeme\s*\+\s*İşçilik Tutarı|K[âa]rsız Malzeme ve Montaj(?: \(TL\))?)\s+(?P<x>{_SAYI})$", re.I)
_KAR_RE = re.compile(rf"^(?:(?P<oran>\d+(?:,\d+)?)\s*%|%\s*(?P<oran2>\d+(?:,\d+)?))\s*Yüklenici k[âa]rı.*?\s(?P<x>{_SAYI})$", re.I)
_FIYAT_RE = re.compile(rf"^(?:(?:\d+\s+)?\S+(?:\s\S+)?\s+Fiyatı|Genel Giderler Dahil Birim Fiyat(?: \(TL\))?)\s+(?P<x>{_SAYI})$")
_SAYFA_GURULTU = re.compile(r"^(Genel Fiyat Analizi|(?:Mekanik|Elektrik) Tesisat(?:ı)? Birim Fiyat Analizi|"
                            r"\d{1,2}\.\d{2}\.\d{4}|-\s*\d+\s*-|Poz No Tanımı.*|Ölçü Malzeme Montaj Tutarı|"
                            r"Birimi Tutarı \(TL\) \(TL\)|K[âa]rsız Toplam.*)$")


def analiz_oku(yol: Path) -> list[Analiz]:
    """Bakanlık analiz kitabını (PDF) okur.

    Her analiz şu düzendedir:
        Poz No Analizin Adı Ölçü Birimi
        15.100.1001 <analizin adı> Ton           (ad, poz satırının üstüne/altına taşabilir)
        Poz No Tanımı Ölçü Birimi Miktarı Birim Fiyatı Tutarı (TL)
        <grup başlığı>                           (ör. "Foraj:", "Beton dökümü karşılığı")
        10.100.1062 Düz işçi Sa 1 205,00 205,00
        Malzeme + İşçilik Tutarı 205,00
        25 % Yüklenici kârı ve genel giderler 51,25
        1 Ton Fiyatı 256,25
        <tarif metni ...>
    """
    import pdfplumber

    analizler: list[Analiz] = []
    durum = "bekle"          # bekle | baslik | kalem
    baslik: list[str] = []
    grup: list[str] = []
    a: Analiz | None = None
    tesisat_bicimi = False   # "Malzeme Bileşenleri / Montaj Bileşenleri" düzeni

    def baslik_coz(satirlar: list[str]) -> Analiz | None:
        # Önce tam ÇŞİDB biçimi (25.452.2101); ad "10.000 m³/h ..." diye başlayabildiği için
        i = next((i for i, x in enumerate(satirlar) if re.fullmatch(r"\d{2}\.\d{3}\.\d{4}", x.split()[0])), None)
        if i is None:
            i = next((i for i, x in enumerate(satirlar) if poz_mu(x.split()[0])), None)
        if i is None:
            return None
        parcalar = satirlar[i].split()
        poz, kalan = parcalar[0], parcalar[1:]
        birim = None
        if kalan and birim_mi(kalan[-1]):
            birim = kalan.pop()
            if kalan and kalan[-1].isdigit():  # "100 m²", "1000 Ad"
                birim = f"{kalan.pop()} {birim}"
        ad = " ".join(satirlar[:i] + [" ".join(kalan)] + satirlar[i + 1:])
        return Analiz(poz, re.sub(r"\s+", " ", ad).strip(), birim, [])

    with pdfplumber.open(yol) as pdf:
        for sayfa in _sayfalar(pdf):
            for satir in (sayfa.extract_text() or "").splitlines():
                satir = satir.strip()
                if not satir:
                    continue
                if satir.startswith("Poz No Analizin Adı"):
                    durum, baslik, a, tesisat_bicimi = "baslik", [], None, False
                    continue
                if durum == "baslik":
                    if satir.startswith("Poz No Tanımı"):
                        a = baslik_coz(baslik)
                        durum, grup = ("kalem", []) if a else ("bekle", [])
                    elif not _SAYFA_GURULTU.match(satir):
                        baslik.append(satir)
                    continue
                if durum != "kalem" or _SAYFA_GURULTU.match(satir):
                    continue
                if m := _ARA_RE.match(satir):
                    a.ara_toplam = sayi(m["x"])
                elif m := _KAR_RE.match(satir):
                    a.kar_orani = round(float((m["oran"] or m["oran2"]).replace(",", ".")) / 100, 4)
                elif (m := _FIYAT_RE.match(satir)) and a.ara_toplam is not None:
                    a.fiyat = sayi(m["x"])
                    analizler.append(a)
                    durum = "bekle"  # ardından gelen tarif metni atlanır
                elif (m := _KALEM_KISA_RE.match(satir)) and poz_mu(m["poz"]):
                    a.kalemler.append(Kalem(" ".join(grup) or None, m["poz"], "", None,
                                            sayi(m["miktar"]), sayi(m["bf"]), sayi(m["tutar"])))
                elif m := _ORAN_KALEM_RE.match(satir):
                    a.kalemler.append(Kalem(" ".join(grup) or None, None, m["tanim"], None,
                                            sayi(m["oran"]), None, sayi(m["tutar"])))
                elif m := _KALEM_RE.match(satir):
                    poz = m["poz"] if m["poz"] and poz_mu(m["poz"]) else None
                    tanim = m["tanim"] if poz else f"{m['poz'] or ''} {m['tanim']}".strip()
                    a.kalemler.append(Kalem(" ".join(grup) or None, poz, tanim, m["birim"],
                                            sayi(m["miktar"]), sayi(m["bf"]), sayi(m["tutar"])))
                elif (tesisat_bicimi and a.kalemler and a.kalemler[-1].grup == " ".join(grup)
                      and not satir.endswith(("Bileşenleri", ":"))):
                    a.kalemler[-1].tanim += " " + satir  # tesisat analizlerinde tanım rakamların altına taşar
                else:
                    if a.kalemler and grup and a.kalemler[-1].grup == " ".join(grup):
                        grup = []  # kalemlerden sonra gelen yeni metin yeni bir grubu başlatır
                    tesisat_bicimi = tesisat_bicimi or satir.endswith("Bileşenleri")
                    grup.append(satir)
    return analizler


def analiz_kontrol(analizler: list[Analiz]) -> dict:
    """Okunan analizlerin tutarlılığını sayar: kalemler toplamı = ara toplam, ara toplam x (1+kâr) = fiyat."""
    sonuc = {"toplam": len(analizler), "kalem_toplami_uyusmayan": [], "fiyat_uyusmayan": []}
    for a in analizler:
        t = sum(k.tutar or 0 for k in a.kalemler)
        if a.ara_toplam is None or abs(t - a.ara_toplam) > max(0.05, a.ara_toplam * 0.001):
            sonuc["kalem_toplami_uyusmayan"].append(a.poz_no)
        if a.fiyat is None or a.ara_toplam is None or a.kar_orani is None or \
                abs(a.ara_toplam * (1 + a.kar_orani) - a.fiyat) > max(0.05, a.fiyat * 0.001):
            sonuc["fiyat_uyusmayan"].append(a.poz_no)
    return sonuc


# ---------------------------------------------------------------- Yükleme

def tur_bul(kurum: str, poz_no: str) -> str | None:
    if kurum.upper() == "CSB":
        return "rayic" if poz_no.startswith("10.") else "birim_fiyat"
    return None


def oku(yol: Path) -> list[Satir]:
    uzanti = yol.suffix.lower()
    if uzanti in (".xlsx", ".xlsm", ".xls", ".csv"):
        return excel_oku(yol)
    if uzanti == ".pdf":
        return pdf_oku(yol)
    raise ValueError(f"Desteklenmeyen dosya türü: {uzanti}")


def _kaynak_ac(con, kurum, yil, donem, liste, icerik, dosya, parca=0) -> int:
    """Aynı kaynak daha önce yüklendiyse siler, yenisini açar."""
    eski = con.execute("SELECT id FROM kaynak WHERE kurum=? AND yil=? AND donem=? AND liste=? AND icerik=? AND parca=?",
                       (kurum, yil, donem, liste, icerik, parca)).fetchone()
    if eski:
        con.execute("DELETE FROM poz_fts WHERE rowid IN (SELECT id FROM poz WHERE kaynak_id=?)", (eski["id"],))
        con.execute("DELETE FROM kaynak WHERE id=?", (eski["id"],))
    return con.execute("INSERT INTO kaynak (kurum, yil, donem, liste, icerik, parca, sira, dosya) VALUES (?,?,?,?,?,?,?,?)",
                       (kurum, yil, donem, liste, icerik, parca, donem_sira(yil, donem), dosya)).lastrowid


def yukle(con, satirlar: list[Satir], kurum: str, yil: int, donem: str = "", dosya: str = "",
          liste: str = "insaat") -> int:
    """Bir birim fiyat listesini (kurum+yıl+dönem+liste) baştan yazar; eklenen poz sayısını döner."""
    kurum = kurum.upper()
    with con:
        kid = _kaynak_ac(con, kurum, yil, donem, liste, "fiyat", dosya)
        eklenen = 0
        for s in satirlar:
            tanim = re.sub(r"\s+", " ", s.tanim).strip()
            cur = con.execute(
                "INSERT OR IGNORE INTO poz (kaynak_id, poz_no, tanim, birim, fiyat, montaj, tur) VALUES (?,?,?,?,?,?,?)",
                (kid, s.poz_no, tanim, s.birim, s.fiyat, s.montaj, tur_bul(kurum, s.poz_no)))
            if cur.rowcount:
                con.execute("INSERT INTO poz_fts (rowid, poz_no, tanim) VALUES (?,?,?)",
                            (cur.lastrowid, katla(s.poz_no), katla(tanim)))
                eklenen += 1
    return eklenen


def analiz_yukle(con, analizler: list["Analiz"], kurum: str, yil: int, donem: str = "", dosya: str = "",
                 liste: str = "insaat", parca: int = 0) -> int:
    """Bir analiz kitabını baştan yazar; eklenen analiz sayısını döner."""
    kurum = kurum.upper()
    with con:
        kid = _kaynak_ac(con, kurum, yil, donem, liste, "analiz", dosya, parca)
        eklenen = 0
        for a in analizler:
            cur = con.execute("""INSERT OR IGNORE INTO analiz (kaynak_id, poz_no, tanim, birim, ara_toplam, kar_orani, fiyat)
                                 VALUES (?,?,?,?,?,?,?)""",
                              (kid, a.poz_no, a.tanim, a.birim, a.ara_toplam, a.kar_orani, a.fiyat))
            if not cur.rowcount:
                continue
            con.executemany("""INSERT INTO analiz_kalem (analiz_id, sira, grup, poz_no, tanim, birim, miktar, birim_fiyat, tutar)
                               VALUES (?,?,?,?,?,?,?,?,?)""",
                            [(cur.lastrowid, i, k.grup, k.poz_no, k.tanim, k.birim, k.miktar, k.birim_fiyat, k.tutar)
                             for i, k in enumerate(a.kalemler)])
            eklenen += 1
    return eklenen


def main(argv=None):
    ap = argparse.ArgumentParser(description="Birim fiyat dosyasını içe aktar")
    ap.add_argument("dosya", type=Path)
    ap.add_argument("--kurum", required=True, help="CSB, KGM, DSI, ILBANK ...")
    ap.add_argument("--yil", required=True, type=int)
    ap.add_argument("--donem", default="", help="Yıl içi güncelleme dönemi (ör. Eylül); boş = yıl başı")
    ap.add_argument("--liste", default="insaat", choices=["insaat", "tesisat", "elektrik"])
    ap.add_argument("--db", default=None)
    a = ap.parse_args(argv)

    satirlar = oku(a.dosya)
    if not satirlar:
        sys.exit("Dosyada poz bulunamadı. Biçim desteklenmiyor olabilir.")
    n = yukle(baglan(a.db), satirlar, a.kurum, a.yil, a.donem, a.dosya.name, a.liste)
    fiyatsiz = sum(1 for s in satirlar if s.fiyat is None)
    print(f"{n} poz yüklendi ({a.kurum} {a.yil} {a.donem} {a.liste}). Fiyatı okunamayan: {fiyatsiz}")


if __name__ == "__main__":
    main()
