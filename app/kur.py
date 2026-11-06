"""veri/csv klasöründeki listeleri veritabanına yükler ve yeni listeleri bu klasöre yazar.

Dosya adı biçimi:
    KURUM_YIL_DONEM_LISTE.csv          birim fiyat listesi   (ör. CSB_2026_Kasim_insaat.csv)
    KURUM_YIL_DONEM_LISTE_analiz.csv   analiz kitabı         (ör. CSB_2026_Yilbasi_tesisat_analiz.csv)
    KURUM_YIL_DONEM_LISTE_analizN.csv  parçalı analiz kitabının N. kitabı
DONEM: Yilbasi, Ocak ... Aralik.  LISTE: insaat, tesisat, elektrik.
Kullanım: python -m app.kur
"""
import csv
from pathlib import Path

from . import importer
from .db import AYLAR, baglan, katla

CSV_KLASORU = Path(__file__).resolve().parent.parent / "veri" / "csv"
_DONEM_ADI = {katla(a): a for a in AYLAR}


def dosya_adi(kurum: str, yil: int, donem: str, liste: str, icerik: str = "fiyat", parca: int = 0) -> str:
    donem_kisa = katla(donem).capitalize() if donem else "Yilbasi"
    ek = f"_analiz{parca or ''}" if icerik == "analiz" else ""
    return f"{kurum.upper()}_{yil}_{donem_kisa}_{liste}{ek}.csv"


def dosya_adini_coz(ad: str) -> dict | None:
    parca = Path(ad).stem.split("_")
    if len(parca) < 4 or not parca[1].isdigit():
        return None
    donem = "" if parca[2].lower() == "yilbasi" else _DONEM_ADI.get(parca[2].lower(), parca[2])
    ek = parca[4] if len(parca) > 4 else ""
    analiz = ek.startswith("analiz")
    return {"kurum": parca[0].upper(), "yil": int(parca[1]), "donem": donem, "liste": parca[3],
            "icerik": "analiz" if analiz else "fiyat",
            "parca": int(ek[6:]) if analiz and ek[6:].isdigit() else 0}


# ---- CSV yazma / okuma

def _tr(x):
    return "" if x is None else f"{x:.2f}".replace(".", ",")


def fiyat_csv_yaz(yol: Path, satirlar) -> None:
    with open(yol, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter=";")
        montajli = any(s.montaj is not None for s in satirlar)
        w.writerow(["Poz No", "Tanımı", "Birimi", "Birim Fiyatı"] + (["Montaj Bedeli"] if montajli else []))
        for s in satirlar:
            w.writerow([s.poz_no, s.tanim, s.birim or "", _tr(s.fiyat)] + ([_tr(s.montaj)] if montajli else []))


ANALIZ_BASLIK = ["Analiz Poz No", "Analiz Tanımı", "Analiz Birimi", "Ara Toplam", "Kâr Oranı", "Fiyat",
                 "Grup", "Poz No", "Tanımı", "Birimi", "Miktarı", "Birim Fiyatı", "Tutarı"]


def analiz_csv_yaz(yol: Path, analizler) -> None:
    """Her kalem bir satır; analizin kendi bilgileri her satırda tekrar eder."""
    s = lambda x: "" if x is None else repr(float(x))
    with open(yol, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(ANALIZ_BASLIK)
        for a in analizler:
            ust = [a.poz_no, a.tanim, a.birim or "", s(a.ara_toplam), s(a.kar_orani), s(a.fiyat)]
            for k in a.kalemler or [importer.Kalem(None, None, "", None, None, None, None)]:
                w.writerow(ust + [k.grup or "", k.poz_no or "", k.tanim, k.birim or "", s(k.miktar), s(k.birim_fiyat), s(k.tutar)])


def analiz_csv_oku(yol: Path) -> list:
    f_ = lambda x: float(x) if x else None
    analizler: dict[str, importer.Analiz] = {}
    with open(yol, encoding="utf-8") as f:
        r = csv.reader(f, delimiter=";")
        next(r)
        for satir in r:
            ap, at, ab, ara, kar, fy, grup, kp, kt, kb, km, kbf, ktu = satir
            a = analizler.setdefault(ap, importer.Analiz(ap, at, ab or None, [], f_(ara), f_(kar), f_(fy)))
            if kt or kp:
                a.kalemler.append(importer.Kalem(grup or None, kp or None, kt, kb or None, f_(km), f_(kbf), f_(ktu)))
    return list(analizler.values())


# ---- Yükleme

def dosya_yukle(con, yol: Path) -> int:
    b = dosya_adini_coz(yol.name)
    if b is None:
        raise ValueError(f"Dosya adı biçimi tanınmadı: {yol.name}")
    if b["icerik"] == "analiz":
        return importer.analiz_yukle(con, analiz_csv_oku(yol), b["kurum"], b["yil"], b["donem"], yol.name,
                                     b["liste"], b["parca"])
    return importer.yukle(con, importer.oku(yol), b["kurum"], b["yil"], b["donem"], yol.name, b["liste"])


def hepsini_yukle(con=None, sadece_eksikler=False, klasor: Path | None = None) -> int:
    con = con or baglan()
    toplam = 0
    for yol in sorted((klasor or CSV_KLASORU).glob("*.csv")):
        b = dosya_adini_coz(yol.name)
        if b is None:
            continue
        if sadece_eksikler and con.execute(
                "SELECT 1 FROM kaynak WHERE kurum=? AND yil=? AND donem=? AND liste=? AND icerik=? AND parca=?",
                (b["kurum"], b["yil"], b["donem"], b["liste"], b["icerik"], b["parca"])).fetchone():
            continue
        n = dosya_yukle(con, yol)
        print(f"{yol.name}: {n} kayıt")
        toplam += n
    return toplam


if __name__ == "__main__":
    hepsini_yukle()
