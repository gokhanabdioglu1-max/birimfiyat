"""Veritabanı şeması ve yardımcılar (SQLite + FTS5).

Veritabanı bir önbellektir: asıl kaynak veri/csv klasöründeki listelerdir.
Şema değişince veritabanı silinip CSV'lerden yeniden kurulur.
"""
import os
import sqlite3
from pathlib import Path

DB_YOLU = Path(os.environ.get("BF_DB", Path(__file__).resolve().parent.parent / "veri" / "birimfiyat.db"))
SEMA_SURUMU = 3

SEMA = """
CREATE TABLE IF NOT EXISTS kaynak (
    id        INTEGER PRIMARY KEY,
    kurum     TEXT NOT NULL,          -- CSB, KGM, DSI, ILBANK ...
    yil       INTEGER NOT NULL,
    donem     TEXT NOT NULL DEFAULT '',     -- '' = yıl başı; 'Kasım' gibi güncel fiyat dönemi
    liste     TEXT NOT NULL DEFAULT 'insaat',  -- insaat | tesisat | elektrik
    icerik    TEXT NOT NULL DEFAULT 'fiyat',   -- fiyat | analiz
    parca     INTEGER NOT NULL DEFAULT 0,      -- parçalı analiz kitaplarında kitap no
    sira      INTEGER NOT NULL DEFAULT 0,      -- yil*100 + ay (yıl başı = 0): en güncel olanı bulmak için
    dosya     TEXT,
    yuklenme  TEXT DEFAULT (datetime('now')),
    UNIQUE (kurum, yil, donem, liste, icerik, parca)
);

CREATE TABLE IF NOT EXISTS poz (
    id         INTEGER PRIMARY KEY,
    kaynak_id  INTEGER NOT NULL REFERENCES kaynak(id) ON DELETE CASCADE,
    poz_no     TEXT NOT NULL,
    tanim      TEXT NOT NULL,
    birim      TEXT,
    fiyat      REAL,                  -- birim fiyat (tesisat listelerinde montaj dahil)
    montaj     REAL,                  -- tesisat listelerindeki ayrı montaj bedeli
    tur        TEXT,                  -- 'birim_fiyat' | 'rayic' | NULL
    UNIQUE (kaynak_id, poz_no)
);
CREATE INDEX IF NOT EXISTS ix_poz_no ON poz(poz_no);

-- Arama dizini: rowid = poz.id, metinler katla() ile normalize edilmiş halde
CREATE VIRTUAL TABLE IF NOT EXISTS poz_fts USING fts5(
    poz_no, tanim, tokenize="unicode61 remove_diacritics 2 tokenchars '.-/'"
);

-- Analiz: bir pozun fiyatının hangi malzeme ve işçilikten oluştuğu
CREATE TABLE IF NOT EXISTS analiz (
    id         INTEGER PRIMARY KEY,
    kaynak_id  INTEGER NOT NULL REFERENCES kaynak(id) ON DELETE CASCADE,
    poz_no     TEXT NOT NULL,
    tanim      TEXT,
    birim      TEXT,
    ara_toplam REAL,                  -- malzeme + işçilik
    kar_orani  REAL,                  -- ör. 0.25
    fiyat      REAL,                  -- kâr dahil birim fiyat
    UNIQUE (kaynak_id, poz_no)
);
CREATE INDEX IF NOT EXISTS ix_analiz_poz ON analiz(poz_no);

CREATE TABLE IF NOT EXISTS analiz_kalem (
    id          INTEGER PRIMARY KEY,
    analiz_id   INTEGER NOT NULL REFERENCES analiz(id) ON DELETE CASCADE,
    sira        INTEGER NOT NULL,
    grup        TEXT,                 -- Malzeme | İşçilik | Makine ...
    poz_no      TEXT,
    tanim       TEXT,
    birim       TEXT,
    miktar      REAL,
    birim_fiyat REAL,
    tutar       REAL
);
CREATE INDEX IF NOT EXISTS ix_kalem_analiz ON analiz_kalem(analiz_id);
"""

AYLAR = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]
LISTELER = {"insaat": "İnşaat", "tesisat": "Tesisat", "elektrik": "Elektrik"}

_TR = str.maketrans({"İ": "i", "I": "ı", "ı": "i", "ş": "s", "Ş": "s", "ğ": "g", "Ğ": "g",
                     "ü": "u", "Ü": "u", "ö": "o", "Ö": "o", "ç": "c", "Ç": "c"})


def katla(metin: str) -> str:
    """Türkçe karakterleri ve büyük/küçük harfi aramaya uygun hale getirir."""
    return (metin or "").translate(_TR).lower().translate(_TR)


def donem_sira(yil: int, donem: str) -> int:
    ay = next((i + 1 for i, a in enumerate(AYLAR) if katla(a) == katla(donem)), 0)
    return yil * 100 + ay


def baglan(yol: Path | str | None = None) -> sqlite3.Connection:
    yol = Path(yol or DB_YOLU)
    yol.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(yol, check_same_thread=False)
    con.row_factory = sqlite3.Row
    if con.execute("PRAGMA user_version").fetchone()[0] != SEMA_SURUMU:
        # Eski şema: tabloları kaldır, CSV'lerden yeniden kurulacak
        for (ad,) in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
                                 "AND name NOT LIKE '%fts_%'").fetchall():
            con.execute(f'DROP TABLE IF EXISTS "{ad}"')
        con.execute(f"PRAGMA user_version = {SEMA_SURUMU}")
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA journal_mode = WAL")  # takip yazarken arama okuyabilsin
    con.execute("PRAGMA busy_timeout = 10000")
    con.executescript(SEMA)
    return con
