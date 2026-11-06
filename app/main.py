"""Poz arama API'si ve web arayüzü."""
import os
import re
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import takip
from .db import LISTELER, baglan, katla
from .kur import hepsini_yukle

STATIC = Path(__file__).resolve().parent.parent / "static"
app = FastAPI(title="Birim Fiyat Platformu")
con = baglan()
if os.environ.get("BF_OTOMATIK_YUKLE", "1") == "1":
    hepsini_yukle(con, sadece_eksikler=True)  # veri/csv'deki listeler veritabanında yoksa yükle
if os.environ.get("BF_TAKIP", "0") == "1":
    takip.arka_planda_baslat()  # baslat.bat açar: Bakanlık sitesinde yeni liste var mı?

# Her kurum + liste için en güncel birim fiyat kaynağı
GUNCEL_KAYNAKLAR = """SELECT k1.id FROM kaynak k1 WHERE k1.icerik = 'fiyat' AND k1.sira =
                        (SELECT MAX(k2.sira) FROM kaynak k2
                         WHERE k2.icerik = 'fiyat' AND k2.kurum = k1.kurum AND k2.liste = k1.liste)"""


def _fts_sorgu(q: str) -> str:
    """Kullanıcı metnini FTS5 önek sorgusuna çevirir: 'beton c30' -> "beton"* "c30"*"""
    kelimeler = re.findall(r"[\w.\-/]+", katla(q))
    return " ".join(f'"{k}"*' for k in kelimeler)


@app.get("/api/kaynaklar")
def kaynaklar():
    rows = con.execute(f"""SELECT k.id, k.kurum, k.yil, k.donem, k.liste, k.icerik, k.dosya, k.yuklenme,
                                  (SELECT COUNT(*) FROM poz p WHERE p.kaynak_id = k.id) AS poz_sayisi,
                                  (SELECT COUNT(*) FROM analiz a WHERE a.kaynak_id = k.id) AS analiz_sayisi,
                                  k.id IN ({GUNCEL_KAYNAKLAR}) AS guncel
                           FROM kaynak k ORDER BY k.sira DESC, k.liste, k.icerik""").fetchall()
    return [dict(r) | {"liste_adi": LISTELER.get(r["liste"], r["liste"])} for r in rows]


@app.get("/api/ara")
def ara(q: str = Query("", max_length=200), kaynak_id: int | None = None, liste: str | None = None,
        tur: str | None = None, limit: int = Query(50, le=500), offset: int = 0):
    kosul, param = [], []
    if kaynak_id:
        kosul.append("p.kaynak_id = ?"); param.append(kaynak_id)
    else:
        kosul.append(f"p.kaynak_id IN ({GUNCEL_KAYNAKLAR})")
    if liste:
        kosul.append("k.liste = ?"); param.append(liste)
    if tur:
        kosul.append("p.tur = ?"); param.append(tur)

    sorgu = _fts_sorgu(q) if q.strip() else ""
    katilim, sira, sira_param = "", "k.liste, p.poz_no", []
    if sorgu:
        katilim = "JOIN poz_fts f ON f.rowid = p.id AND poz_fts MATCH ?"
        param.insert(0, sorgu)
        # poz no ile başlayan aramada önce o pozlar, yoksa en ilgili tanımlar
        sira = "CASE WHEN p.poz_no LIKE ? THEN 0 ELSE 1 END, bm25(poz_fts, 10.0, 1.0), p.poz_no"
        sira_param = [q.strip() + "%"]
    where = " AND ".join(kosul)
    temel = f"FROM poz p {katilim} JOIN kaynak k ON k.id = p.kaynak_id WHERE {where}"
    toplam = con.execute(f"SELECT COUNT(*) {temel}", param).fetchone()[0]
    rows = con.execute(f"""SELECT p.id, p.poz_no, p.tanim, p.birim, p.fiyat, p.montaj, p.tur,
                                  k.kurum, k.yil, k.donem, k.liste,
                                  EXISTS (SELECT 1 FROM analiz a WHERE a.poz_no = p.poz_no) AS analiz_var
                           {temel} ORDER BY {sira} LIMIT ? OFFSET ?""",
                       [*param, *sira_param, limit, offset]).fetchall()
    return {"toplam": toplam, "sonuclar": [dict(r) for r in rows]}


@app.get("/api/poz/{poz_no:path}")
def poz_detay(poz_no: str):
    """Pozun tüm yıl/dönemlerdeki fiyatları ve en güncel analizi."""
    gecmis = con.execute("""SELECT p.poz_no, p.tanim, p.birim, p.fiyat, p.montaj, k.kurum, k.yil, k.donem, k.liste
                            FROM poz p JOIN kaynak k ON k.id = p.kaynak_id
                            WHERE p.poz_no = ? ORDER BY k.sira, k.yuklenme""", (poz_no,)).fetchall()
    a = con.execute("""SELECT a.*, k.yil, k.donem FROM analiz a JOIN kaynak k ON k.id = a.kaynak_id
                       WHERE a.poz_no = ? ORDER BY k.sira DESC LIMIT 1""", (poz_no,)).fetchone()
    if not gecmis and not a:
        raise HTTPException(404, "Poz bulunamadı")
    analiz = None
    if a:
        kalemler = con.execute("""SELECT grup, poz_no, tanim, birim, miktar, birim_fiyat, tutar
                                  FROM analiz_kalem WHERE analiz_id = ? ORDER BY sira""", (a["id"],)).fetchall()
        analiz = dict(a) | {"kalemler": [dict(k) for k in kalemler]}
    return {"gecmis": [dict(r) for r in gecmis], "analiz": analiz}


@app.get("/api/takip")
def takip_durumu():
    d = takip.durum_oku()
    dosyalar = sorted(d.get("dosyalar", {}).values(), key=lambda x: (x.get("yil", 0), x.get("tarih", "")), reverse=True)
    return {"calisiyor": takip.calisiyor, "son_kontrol": d.get("son_kontrol"),
            "son_ozet": d.get("son_ozet"), "dosyalar": dosyalar[:100]}


@app.post("/api/takip")
def takip_baslat():
    return {"basladi": takip.arka_planda_baslat()}


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
