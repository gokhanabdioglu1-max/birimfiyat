"""veri/csv klasöründeki hazır listeleri veritabanına yükler.

Dosya adı biçimi: KURUM_YIL[_DONEM].csv  (ör. CSB_2026_Kasim.csv)
Kullanım: python -m app.kur
"""
from pathlib import Path

from . import importer
from .db import baglan

CSV_KLASORU = Path(__file__).resolve().parent.parent / "veri" / "csv"
DONEMLER = {"ocak": "Ocak", "subat": "Şubat", "mart": "Mart", "nisan": "Nisan", "mayis": "Mayıs",
            "haziran": "Haziran", "temmuz": "Temmuz", "agustos": "Ağustos", "eylul": "Eylül",
            "ekim": "Ekim", "kasim": "Kasım", "aralik": "Aralık"}


def hepsini_yukle(con=None, sadece_eksikler=False) -> int:
    con = con or baglan()
    toplam = 0
    for yol in sorted(CSV_KLASORU.glob("*.csv")):
        parca = yol.stem.split("_")
        kurum, yil = parca[0], int(parca[1])
        donem = DONEMLER.get(parca[2].lower(), parca[2]) if len(parca) > 2 else ""
        if sadece_eksikler and con.execute("SELECT 1 FROM kaynak WHERE kurum=? AND yil=? AND donem=?",
                                           (kurum.upper(), yil, donem)).fetchone():
            continue
        n = importer.yukle(con, importer.oku(yol), kurum, yil, donem, yol.name)
        print(f"{yol.name}: {n} poz")
        toplam += n
    return toplam


if __name__ == "__main__":
    hepsini_yukle()
