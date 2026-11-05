"""Veritabanı şeması ve yardımcılar (SQLite + FTS5)."""
import os
import sqlite3
from pathlib import Path

DB_YOLU = Path(os.environ.get("BF_DB", Path(__file__).resolve().parent.parent / "veri" / "birimfiyat.db"))

SEMA = """
CREATE TABLE IF NOT EXISTS kaynak (
    id        INTEGER PRIMARY KEY,
    kurum     TEXT NOT NULL,          -- CSB, KGM, DSI, ILBANK ...
    yil       INTEGER NOT NULL,
    donem     TEXT NOT NULL DEFAULT '',  -- '' = yıl başı; 'Eylül' gibi güncelleme dönemi
    dosya     TEXT,
    yuklenme  TEXT DEFAULT (datetime('now')),
    UNIQUE (kurum, yil, donem)
);

CREATE TABLE IF NOT EXISTS poz (
    id         INTEGER PRIMARY KEY,
    kaynak_id  INTEGER NOT NULL REFERENCES kaynak(id) ON DELETE CASCADE,
    poz_no     TEXT NOT NULL,
    tanim      TEXT NOT NULL,
    birim      TEXT,
    fiyat      REAL,
    tur        TEXT,                  -- 'birim_fiyat' | 'rayic' | NULL
    UNIQUE (kaynak_id, poz_no)
);
CREATE INDEX IF NOT EXISTS ix_poz_no ON poz(poz_no);

-- Arama dizini: rowid = poz.id, metinler katla() ile normalize edilmiş halde
CREATE VIRTUAL TABLE IF NOT EXISTS poz_fts USING fts5(
    poz_no, tanim, tokenize="unicode61 remove_diacritics 2 tokenchars '.-/'"
);
"""

_TR = str.maketrans({"İ": "i", "I": "ı", "ı": "i", "ş": "s", "Ş": "s", "ğ": "g", "Ğ": "g",
                     "ü": "u", "Ü": "u", "ö": "o", "Ö": "o", "ç": "c", "Ç": "c"})


def katla(metin: str) -> str:
    """Türkçe karakterleri ve büyük/küçük harfi aramaya uygun hale getirir."""
    return (metin or "").translate(_TR).lower().translate(_TR)


def baglan(yol: Path | str | None = None) -> sqlite3.Connection:
    yol = Path(yol or DB_YOLU)
    yol.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(yol, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript(SEMA)
    return con
