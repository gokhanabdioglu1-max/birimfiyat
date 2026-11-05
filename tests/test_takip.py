"""Otomatik takip testleri: sahte bir Bakanlık sayfası üzerinden. Pozlar ve fiyatlar UYDURMADIR."""
import io
import json

import pandas as pd
import pytest

from app import importer, kur, takip
from app.db import baglan


def _excel(n=150, fiyat=100.0, bos_fiyat=0) -> bytes:
    satirlar = [["Poz No", "Tanımı", "Birimi", "Birim Fiyatı"]]
    for i in range(n):
        satirlar.append([f"35.100.{9000 + i}", f"Test elektrik kalemi {i}", "m", None if i < bos_fiyat else fiyat + i])
    b = io.BytesIO()
    pd.DataFrame(satirlar).to_excel(b, header=False, index=False)
    return b.getvalue()


ANA = """<html><body>
<a href="/birim-fiyatlar">Birim Fiyatlar</a>
<a href="/hakkimizda">Hakkımızda</a>
<a href="https://baska-site.com/2026-elektrik-birim-fiyat.xlsx">2026 Elektrik Birim Fiyatları</a>
</body></html>"""
ALT = """<html><body>
<p>2026 Yılı Kasım Ayı Elektrik Tesisatı Güncel Birim Fiyatları</p><a href="https://webdosya.csb.gov.tr/x/eb.xlsx">İndir</a>
<a href="https://webdosya.csb.gov.tr/x/eb-en.xlsx">2026 Electrical Unit Prices (İngilizce) birim fiyat</a>
<a href="https://webdosya.csb.gov.tr/x/bozuk.xlsx">2026 Yılı Mekanik Tesisat Birim Fiyatları</a>
<a href="https://webdosya.csb.gov.tr/x/analiz.pdf">2026 Yılı İnşaat Analizleri</a>
<a href="https://webdosya.csb.gov.tr/x/ymaliyet.pdf">2026 Yapı Yaklaşık Birim Maliyetleri</a>
</body></html>"""


@pytest.fixture
def ortam(tmp_path, monkeypatch):
    monkeypatch.setattr(takip, "DURUM_DOSYASI", tmp_path / "durum.json")
    istekler = []
    dosyalar = {
        "https://yfk.csb.gov.tr/": ANA.encode(),
        "https://yfk.csb.gov.tr/birim-fiyatlar": ALT.encode(),
        "https://webdosya.csb.gov.tr/x/eb.xlsx": _excel(),
        "https://webdosya.csb.gov.tr/x/bozuk.xlsx": _excel(bos_fiyat=50),
        "https://webdosya.csb.gov.tr/x/analiz.pdf": b"%PDF-1.4",
    }

    def istek(url):
        istekler.append(url)
        if url not in dosyalar:
            raise OSError("404")
        return dosyalar[url]

    con = baglan(tmp_path / "t.db")
    calistir = lambda: takip.calistir(con, dict(takip.VARSAYILAN_AYAR), istek, lambda *_: None,
                                      tmp_path / "csv", tmp_path / "indirilen")
    return con, calistir, istekler, tmp_path


def test_siniflandir():
    s = takip.siniflandir("2026 Yılı Kasım Ayı İnşaat Güncel Birim Fiyatları", "x.pdf")
    assert s == {"kurum": "CSB", "yil": 2026, "donem": "Kasım", "liste": "insaat", "icerik": "fiyat"}
    assert takip.siniflandir("2026 Mekanik Tesisat Analizleri", "a.pdf")["icerik"] == "analiz"
    assert takip.siniflandir("2026 Elektrik Tesisatı Birim Fiyatları", "")["liste"] == "elektrik"
    assert takip.siniflandir("2026 Mekanik Tesisat Birim Fiyatları", "")["liste"] == "tesisat"
    assert takip.siniflandir("2026 İnşaat ve Tesisat Birim Fiyatları", "")["liste"] == "insaat"
    assert takip.siniflandir("2026 Yılı İnşaat Birim Fiyatları", "")["donem"] == ""
    assert takip.siniflandir("2026 Yapı Yaklaşık Birim Maliyetleri", "") is None
    assert takip.siniflandir("2026 İnşaat Birim Fiyatları (İngilizce)", "") is None
    assert takip.siniflandir("2019 İnşaat Birim Fiyatları", "") is None  # çok eski


def test_takip_turu(ortam):
    con, calistir, istekler, tmp = ortam
    ozet = calistir()
    assert ozet["bulunan"] == 3  # elektrik, bozuk tesisat, analiz (yabancı site, İngilizce, yaklaşık maliyet hariç)
    assert ozet["yeni"] == ["CSB_2026_Kasim_elektrik.csv: 150 kayıt"]
    assert any("fiyatı okunamadı" in h for h in ozet["hatalar"])
    assert "https://baska-site.com/2026-elektrik-birim-fiyat.xlsx" not in istekler
    assert (tmp / "csv" / "CSB_2026_Kasim_elektrik.csv").exists()
    k = con.execute("SELECT * FROM kaynak WHERE liste='elektrik'").fetchone()
    assert (k["yil"], k["donem"], k["sira"]) == (2026, "Kasım", 202611)

    d = json.loads((tmp / "durum.json").read_text(encoding="utf-8"))["dosyalar"]
    assert d["https://webdosya.csb.gov.tr/x/eb.xlsx"]["durum"] == "yuklendi"
    assert d["https://webdosya.csb.gov.tr/x/bozuk.xlsx"]["durum"] == "reddedildi"
    assert d["https://webdosya.csb.gov.tr/x/analiz.pdf"]["durum"] == "bekliyor"

    # ikinci tur: işlenmiş dosyalar tekrar indirilmez
    istekler.clear()
    ozet2 = calistir()
    assert ozet2["yeni"] == []
    assert not any(u.endswith((".xlsx",)) for u in istekler)


def test_site_kapali(tmp_path, monkeypatch):
    monkeypatch.setattr(takip, "DURUM_DOSYASI", tmp_path / "durum.json")
    def istek(url): raise OSError("Connection reset")
    ozet = takip.calistir(baglan(tmp_path / "t.db"), dict(takip.VARSAYILAN_AYAR), istek, lambda *_: None,
                          tmp_path / "csv", tmp_path / "ind")
    assert ozet["yeni"] == [] and "ulaşılamadı" in ozet["hatalar"][0]


def test_analiz_csv_ve_api(tmp_path, monkeypatch):
    a = importer.Analiz("15.999.9001", "Test analizi", "m³", [
        importer.Kalem("Malzeme", "10.130.9001", "Test çimento", "Ton", 0.3, 3000.0, 900.0),
        importer.Kalem("İşçilik", "10.100.9001", "Test işçi", "Sa", 2.0, 250.0, 500.0),
    ], 1400.0, 0.25, 1750.0)
    klasor = tmp_path / "csv"; klasor.mkdir()
    yol = klasor / kur.dosya_adi("CSB", 2026, "", "insaat", "analiz")
    kur.analiz_csv_yaz(yol, [a])
    assert kur.analiz_csv_oku(yol) == [a]

    monkeypatch.setenv("BF_DB", str(tmp_path / "t.db"))
    monkeypatch.setenv("BF_OTOMATIK_YUKLE", "0")
    import importlib
    from app import db
    importlib.reload(db)
    from app import main
    importlib.reload(main)
    kur.hepsini_yukle(main.con, klasor=klasor)
    importer.yukle(main.con, [importer.Satir("15.999.9001", "Test analizi", "m³", 1750.0)], "CSB", 2026)
    from fastapi.testclient import TestClient
    c = TestClient(main.app)
    assert c.get("/api/ara", params={"q": "test analizi"}).json()["sonuclar"][0]["analiz_var"] == 1
    d = c.get("/api/poz/15.999.9001").json()
    assert d["analiz"]["fiyat"] == 1750.0 and len(d["analiz"]["kalemler"]) == 2
    assert c.get("/api/takip").status_code == 200
