"""İçe aktarma ve arama testleri. Buradaki pozlar ve fiyatlar UYDURMADIR, sadece test içindir."""
import importlib

import pandas as pd
import pytest

from app import importer
from app.db import baglan, katla


@pytest.fixture
def con(tmp_path):
    return baglan(tmp_path / "t.db")


def test_sayi():
    assert importer.sayi("1.234,56") == 1234.56
    assert importer.sayi("12,5") == 12.5
    assert importer.sayi("1.250") == 1250.0
    assert importer.sayi(99.9) == 99.9
    assert importer.sayi("abc") is None


def test_poz_kalibi():
    for p in ["15.150.1005", "10.130.1001", "Y.16.050/01", "04.613/1A", "KGM-07.001"]:
        assert importer.poz_mu(p), p
    for p in ["Beton", "2026", "m³"]:
        assert not importer.poz_mu(p), p


def test_katla():
    assert katla("IŞIK İnce ÇİMENTO") == "isik ince cimento"


def test_excel_baslikli(tmp_path):
    df = pd.DataFrame([
        ["ÖRNEK BİRİM FİYAT LİSTESİ", None, None, None, None],
        ["Sıra", "Poz No", "Tanımı", "Birimi", "Birim Fiyatı (TL)"],
        [1, "15.150.9001", "Test betonu C30/37", "m³", 1234.5],
        [2, "15.275.9002", "Test alçı sıva", "m²", "456,75"],
        [None, "Ara başlık", None, None, None],
    ])
    yol = tmp_path / "a.xlsx"; df.to_excel(yol, header=False, index=False)
    s = importer.oku(yol)
    assert [(x.poz_no, x.birim, x.fiyat) for x in s] == [("15.150.9001", "m³", 1234.5), ("15.275.9002", "m²", 456.75)]


def test_csv_basliksiz(tmp_path):
    yol = tmp_path / "b.csv"
    yol.write_text("10.130.9001;Test çimentosu torba;ton;3.250,00\n;devam satırı;;\n", encoding="utf-8")
    s = importer.oku(yol)
    assert len(s) == 1 and s[0].fiyat == 3250.0 and s[0].birim == "ton"


def test_yukle_ve_ara(con, tmp_path, monkeypatch):
    sat = [importer.Satir("15.150.9001", "Test betonu C30/37 dökülmesi", "m³", 1000.0),
           importer.Satir("10.130.9001", "Test çimentosu", "ton", 3000.0),
           importer.Satir("15.275.9002", "Test alçı sıva yapılması", "m²", 400.0)]
    importer.yukle(con, sat, "CSB", 2025)
    importer.yukle(con, [importer.Satir("15.150.9001", "Test betonu C30/37 dökülmesi", "m³", 1300.0)], "CSB", 2026)
    # aynı kaynağı tekrar yüklemek çoğaltma yapmamalı
    importer.yukle(con, [importer.Satir("15.150.9001", "Test betonu C30/37 dökülmesi", "m³", 1300.0)], "CSB", 2026)
    assert con.execute("SELECT COUNT(*) FROM poz_fts").fetchone()[0] == 4

    monkeypatch.setenv("BF_DB", str(tmp_path / "t.db"))
    monkeypatch.setenv("BF_OTOMATIK_YUKLE", "0")
    from app import db
    importlib.reload(db)
    from app import main
    importlib.reload(main)
    from fastapi.testclient import TestClient
    c = TestClient(main.app)

    assert len(c.get("/api/kaynaklar").json()) == 2
    r = c.get("/api/ara", params={"q": "betonu"}).json()          # varsayılan en güncel yıl
    assert r["toplam"] == 1 and r["sonuclar"][0]["fiyat"] == 1300.0
    k25 = [k for k in c.get("/api/kaynaklar").json() if k["yil"] == 2025][0]["id"]
    assert c.get("/api/ara", params={"q": "ALCI", "kaynak_id": k25}).json()["toplam"] == 1   # Türkçe karakter katlama
    assert c.get("/api/ara", params={"q": "15.150", "kaynak_id": k25}).json()["toplam"] == 1  # poz no öneki
    assert c.get("/api/ara", params={"kaynak_id": k25, "tur": "rayic"}).json()["toplam"] == 1
    g = c.get("/api/poz/15.150.9001").json()
    assert [x["fiyat"] for x in g] == [1000.0, 1300.0]
    assert c.get("/").status_code == 200


def test_hazir_csb_listesi(con):
    """Repodaki CSB 2026 Kasım listesi: PDF'ten okunan 1878 pozun tamamı yüklenmeli."""
    from app.kur import hepsini_yukle
    hepsini_yukle(con)
    k = con.execute("SELECT id FROM kaynak WHERE kurum='CSB' AND yil=2026 AND donem='Kasım'").fetchone()
    assert k
    assert con.execute("SELECT COUNT(*) FROM poz WHERE kaynak_id=?", (k["id"],)).fetchone()[0] == 1878
    r = con.execute("SELECT birim, fiyat FROM poz WHERE kaynak_id=? AND poz_no='15.100.1001'", (k["id"],)).fetchone()
    assert (r["birim"], r["fiyat"]) == ("Ton", 311.70)
