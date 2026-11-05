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

from .db import baglan, katla

POZ_RE = re.compile(r"^(?:[A-ZÇĞİÖŞÜ]{1,5}[.\-/ ]?)?\d{1,4}(?:[./\-]\d{1,5}){1,4}(?:/[A-Z0-9]{1,4})?[A-Z]?$")
SAYI_RE = re.compile(r"^\d{1,3}(?:\.\d{3})*(?:,\d+)?$|^\d+(?:,\d+)?$|^\d+\.\d+$")
BIRIMLER = {
    "m", "m2", "m²", "m3", "m³", "m1", "mt", "km", "cm", "mm", "kg", "gr", "ton", "t", "ad", "ad.", "adet",
    "sa", "sa.", "saat", "gün", "gun", "ay", "lt", "l", "litre", "takım", "tk", "set", "kwh", "kw", "kva",
    "dm3", "dm³", "m²/ay", "m2/ay", "m3-km", "m³-km", "ton-km", "ton.km", "sefer", "nokta", "parça", "götürü",
}


@dataclass
class Satir:
    poz_no: str
    tanim: str
    birim: str | None
    fiyat: float | None


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
    elif s.count(".") > 1 or re.match(r"^\d{1,3}\.\d{3}$", s):
        s = s.replace(".", "")
    try:
        return float(s)
    except ValueError:
        return None


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
        for alan, adlar in BASLIKLAR.items():
            if alan not in eslesme and any(h == katla(a) or h.startswith(katla(a) + " ") for a in adlar):
                eslesme[alan] = i
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
                sonuc.append(Satir(str(poz).strip(), str(tanim or "").strip(),
                                   str(birim).strip() if birim else None, sayi(hucreler[eslesme["fiyat"]])))
            elif s := _satir_tahmin(hucreler):
                sonuc.append(s)
    return sonuc


# ---------------------------------------------------------------- PDF

PDF_SATIR_RE = re.compile(
    r"^(?P<poz>\S+)\s+(?P<tanim>.+?)\s+(?P<birim>\S+)\s+(?P<fiyat>\d{1,3}(?:\.\d{3})*,\d{2})$"
)


def pdf_oku(yol: Path) -> list[Satir]:
    """PDF birim fiyat listesini okur.

    Tablo çizgileri olan sayfalarda (Bakanlık listeleri böyle) her satır, yatay
    kenarlıklar arasındaki bant olarak okunur; böylece poz numarasının üstüne
    ve altına taşan uzun tanımlar doğru poza bağlanır. Çizgisiz sayfalarda
    satır satır metin okumaya geri dönülür.
    """
    import pdfplumber

    sonuc: list[Satir] = []
    with pdfplumber.open(yol) as pdf:
        for sayfa in pdf.pages:
            satirlar = _pdf_tablo_sayfasi(sayfa)
            sonuc.extend(satirlar if satirlar is not None else _pdf_metin_sayfasi(sayfa, sonuc))
    return sonuc


def _kumele(degerler, tolerans):
    """Birbirine yakın koordinatları tek değerde birleştirir."""
    gruplar: list[list[float]] = []
    for d in sorted(degerler):
        if gruplar and d - gruplar[-1][-1] <= tolerans:
            gruplar[-1].append(d)
        else:
            gruplar.append([d])
    return [sum(g) / len(g) for g in gruplar]


def _pdf_tablo_sayfasi(sayfa) -> list[Satir] | None:
    from collections import Counter

    dikey = [r for r in sayfa.rects if r["width"] < 2 and r["height"] > 2]
    yatay = [r for r in sayfa.rects if r["height"] < 2 and r["width"] > 2]
    if len(dikey) < 5 or len(yatay) < 5:
        return None
    # Sütun sınırları: en sık görülen 5 dikey çizgi (sol kenar, poz|tanım, tanım|birim, birim|fiyat, sağ kenar)
    xs = _kumele([x for x, _ in Counter(round(r["x0"]) for r in dikey).most_common(8)], 3)
    if len(xs) < 5:
        return None
    xs = sorted(xs)[:5] if len(xs) == 5 else _en_iyi_5(xs, dikey)
    sinir = xs  # [sol, poz/tanım, tanım/birim, birim/fiyat, sağ]

    ys = _kumele([r["top"] for r in yatay], 3)
    kelimeler = sayfa.extract_words(keep_blank_chars=False, use_text_flow=False)
    sonuc = []
    for ust, alt in zip(ys, ys[1:]):
        bant = [w for w in kelimeler if ust <= (w["top"] + w["bottom"]) / 2 < alt]
        if not bant:
            continue
        sutun = {0: [], 1: [], 2: [], 3: []}
        for w in bant:
            orta = (w["x0"] + w["x1"]) / 2
            for i in range(4):
                if sinir[i] - 2 <= orta < sinir[i + 1] + 2:
                    sutun[i].append(w)
                    break
        poz = " ".join(w["text"] for w in sutun[0]).strip()
        if not poz_mu(poz):
            continue  # başlık / bölüm adı satırı
        sonuc.append(Satir(poz, _satir_metni(sutun[1]), _satir_metni(sutun[2]) or None,
                           sayi(_satir_metni(sutun[3]))))
    return sonuc


def _en_iyi_5(xs, dikey):
    from collections import Counter
    sayac = Counter()
    for r in dikey:
        for x in xs:
            if abs(r["x0"] - x) <= 3:
                sayac[x] += 1
    return sorted(x for x, _ in sayac.most_common(5))


def _satir_metni(kelimeler) -> str:
    """Kelimeleri okuma sırasına (yukarıdan aşağı, soldan sağa) dizip birleştirir."""
    kelimeler = sorted(kelimeler, key=lambda w: (round(w["top"]), w["x0"]))
    return re.sub(r"\s+", " ", " ".join(w["text"] for w in kelimeler)).strip()


def _pdf_metin_sayfasi(sayfa, onceki: list[Satir]) -> list[Satir]:
    sonuc: list[Satir] = []
    for satir in (sayfa.extract_text() or "").splitlines():
        satir = satir.strip()
        m = PDF_SATIR_RE.match(satir)
        if m and poz_mu(m["poz"]):
            tanim, birim = m["tanim"], m["birim"]
            if not birim_mi(birim):  # birim sütunu boşsa son kelime tanıma aittir
                tanim, birim = f"{tanim} {birim}", None
            sonuc.append(Satir(m["poz"], tanim, birim, sayi(m["fiyat"])))
        elif (sonuc or onceki) and satir and not poz_mu(satir.split()[0]) and not _sayfa_ustu(satir):
            (sonuc or onceki)[-1].tanim += " " + satir
    return sonuc


def _sayfa_ustu(satir: str) -> bool:
    k = katla(satir)
    return k.startswith(("poz no", "sayfa", "birim fiyat")) or bool(re.fullmatch(r"[\d\s/\-]+", satir))


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


def yukle(con, satirlar: list[Satir], kurum: str, yil: int, donem: str = "", dosya: str = "") -> int:
    """Kaynağı (kurum+yıl+dönem) baştan yazar; eklenen poz sayısını döner."""
    kurum = kurum.upper()
    with con:
        eski = con.execute("SELECT id FROM kaynak WHERE kurum=? AND yil=? AND donem=?", (kurum, yil, donem)).fetchone()
        if eski:
            con.execute("DELETE FROM poz_fts WHERE rowid IN (SELECT id FROM poz WHERE kaynak_id=?)", (eski["id"],))
            con.execute("DELETE FROM kaynak WHERE id=?", (eski["id"],))
        kid = con.execute("INSERT INTO kaynak (kurum, yil, donem, dosya) VALUES (?,?,?,?)",
                          (kurum, yil, donem, dosya)).lastrowid
        eklenen = 0
        for s in satirlar:
            tanim = re.sub(r"\s+", " ", s.tanim).strip()
            cur = con.execute(
                "INSERT OR IGNORE INTO poz (kaynak_id, poz_no, tanim, birim, fiyat, tur) VALUES (?,?,?,?,?,?)",
                (kid, s.poz_no, tanim, s.birim, s.fiyat, tur_bul(kurum, s.poz_no)))
            if cur.rowcount:
                con.execute("INSERT INTO poz_fts (rowid, poz_no, tanim) VALUES (?,?,?)",
                            (cur.lastrowid, katla(s.poz_no), katla(tanim)))
                eklenen += 1
    return eklenen


def main(argv=None):
    ap = argparse.ArgumentParser(description="Birim fiyat dosyasını içe aktar")
    ap.add_argument("dosya", type=Path)
    ap.add_argument("--kurum", required=True, help="CSB, KGM, DSI, ILBANK ...")
    ap.add_argument("--yil", required=True, type=int)
    ap.add_argument("--donem", default="", help="Yıl içi güncelleme dönemi (ör. Eylül); boş = yıl başı")
    ap.add_argument("--db", default=None)
    a = ap.parse_args(argv)

    satirlar = oku(a.dosya)
    if not satirlar:
        sys.exit("Dosyada poz bulunamadı. Biçim desteklenmiyor olabilir.")
    n = yukle(baglan(a.db), satirlar, a.kurum, a.yil, a.donem, a.dosya.name)
    fiyatsiz = sum(1 for s in satirlar if s.fiyat is None)
    print(f"{n} poz yüklendi ({a.kurum} {a.yil} {a.donem}). Fiyatı okunamayan: {fiyatsiz}")


if __name__ == "__main__":
    main()
