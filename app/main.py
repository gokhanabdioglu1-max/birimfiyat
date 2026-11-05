"""Poz arama API'si ve web arayüzü."""
import os
import re
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .db import baglan, katla
from .kur import hepsini_yukle

STATIC = Path(__file__).resolve().parent.parent / "static"
app = FastAPI(title="Birim Fiyat Platformu")
con = baglan()
if os.environ.get("BF_OTOMATIK_YUKLE", "1") == "1":
    hepsini_yukle(con, sadece_eksikler=True)  # repodaki hazır listeler veritabanında yoksa yükle


def _fts_sorgu(q: str) -> str:
    """Kullanıcı metnini FTS5 önek sorgusuna çevirir: 'beton c30' -> "beton"* "c30"*"""
    kelimeler = re.findall(r"[\w.\-/]+", katla(q))
    return " ".join(f'"{k}"*' for k in kelimeler)


@app.get("/api/kaynaklar")
def kaynaklar():
    rows = con.execute("""SELECT k.id, k.kurum, k.yil, k.donem, k.dosya, k.yuklenme, COUNT(p.id) AS poz_sayisi
                          FROM kaynak k LEFT JOIN poz p ON p.kaynak_id = k.id
                          GROUP BY k.id ORDER BY k.yil DESC, k.kurum, k.donem DESC""").fetchall()
    return [dict(r) for r in rows]


@app.get("/api/ara")
def ara(q: str = Query("", max_length=200), kaynak_id: int | None = None, tur: str | None = None,
        limit: int = Query(50, le=500), offset: int = 0):
    kosul, param = [], []
    if kaynak_id:
        kosul.append("p.kaynak_id = ?"); param.append(kaynak_id)
    else:  # varsayılan: en güncel kaynak
        kosul.append("p.kaynak_id = (SELECT id FROM kaynak ORDER BY yil DESC, yuklenme DESC LIMIT 1)")
    if tur:
        kosul.append("p.tur = ?"); param.append(tur)

    sorgu = _fts_sorgu(q) if q.strip() else ""
    katilim, sira = "", "p.poz_no"
    if sorgu:
        katilim = "JOIN poz_fts f ON f.rowid = p.id AND poz_fts MATCH ?"
        param.insert(0, sorgu)
        # poz no ile başlayan aramada önce o pozlar, yoksa en ilgili tanımlar
        sira = "CASE WHEN p.poz_no LIKE ? THEN 0 ELSE 1 END, bm25(poz_fts, 10.0, 1.0), p.poz_no"
    where = " AND ".join(kosul)
    toplam = con.execute(f"SELECT COUNT(*) FROM poz p {katilim} WHERE {where}", param).fetchone()[0]
    sira_param = [q.strip() + "%"] if sorgu else []
    rows = con.execute(f"""SELECT p.id, p.poz_no, p.tanim, p.birim, p.fiyat, p.tur, k.kurum, k.yil, k.donem
                           FROM poz p {katilim} JOIN kaynak k ON k.id = p.kaynak_id
                           WHERE {where}
                           ORDER BY {sira}
                           LIMIT ? OFFSET ?""", [*param, *sira_param, limit, offset]).fetchall()
    return {"toplam": toplam, "sonuclar": [dict(r) for r in rows]}


@app.get("/api/poz/{poz_no:path}")
def poz_gecmisi(poz_no: str):
    """Bir pozun tüm yıl/dönemlerdeki fiyatları (yıllar arası karşılaştırma)."""
    rows = con.execute("""SELECT p.poz_no, p.tanim, p.birim, p.fiyat, k.kurum, k.yil, k.donem
                          FROM poz p JOIN kaynak k ON k.id = p.kaynak_id
                          WHERE p.poz_no = ? ORDER BY k.yil, k.yuklenme""", (poz_no,)).fetchall()
    if not rows:
        raise HTTPException(404, "Poz bulunamadı")
    return [dict(r) for r in rows]


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
