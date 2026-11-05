"""Bibliothek: Ordner scannen, Metadaten in SQLite cachen, Cover-Thumbnails erzeugen."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage

from .epub import read_epub

APP_ID = "buecherregal"


def data_dir() -> Path:
    base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    p = base / APP_ID
    p.mkdir(parents=True, exist_ok=True)
    return p


def cover_dir() -> Path:
    base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    p = base / APP_ID / "covers"
    p.mkdir(parents=True, exist_ok=True)
    return p


def default_library_dir() -> Path:
    return Path.home() / "Bücher"


@dataclass
class Book:
    path: str
    title: str
    authors: list[str] = field(default_factory=list)
    language: str = ""
    description: str = ""
    publisher: str = ""
    date: str = ""
    series: str = ""
    series_index: str = ""
    subjects: list[str] = field(default_factory=list)
    isbn: str = ""
    cover_path: str = ""
    size: int = 0
    mtime: float = 0.0
    added: float = 0.0

    @property
    def author_str(self) -> str:
        return ", ".join(self.authors) if self.authors else "Unbekannt"

    @property
    def is_german(self) -> bool:
        return self.language.split("-")[0] in ("de", "deu", "ger")

    @property
    def series_str(self) -> str:
        if not self.series:
            return ""
        return f"{self.series} #{self.series_index}" if self.series_index else self.series


SCHEMA = """
CREATE TABLE IF NOT EXISTS books (
    path TEXT PRIMARY KEY,
    mtime REAL, size INTEGER, added REAL,
    data TEXT
);
"""


class LibraryDB:
    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or data_dir() / "library.db"
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.execute(SCHEMA)
        self.conn.commit()

    def _cached(self) -> dict[str, tuple[float, int, float, str]]:
        rows = self.conn.execute("SELECT path, mtime, size, added, data FROM books").fetchall()
        return {r[0]: (r[1], r[2], r[3], r[4]) for r in rows}

    def scan(self, folder: str, progress=None) -> list[Book]:
        """Ordner rekursiv scannen. Nur geänderte Dateien werden neu eingelesen."""
        folder_p = Path(folder)
        folder_p.mkdir(parents=True, exist_ok=True)
        files = sorted(
            str(p) for p in folder_p.rglob("*")
            if p.is_file() and p.suffix.lower() == ".epub" and not p.name.startswith(".")
        )
        cached = self._cached()
        books: list[Book] = []
        seen = set()
        for n, f in enumerate(files):
            seen.add(f)
            try:
                st = os.stat(f)
            except OSError:
                continue
            c = cached.get(f)
            if c and abs(c[0] - st.st_mtime) < 1e-3 and c[1] == st.st_size:
                b = Book(**json.loads(c[3]))
                if not b.cover_path or Path(b.cover_path).exists():
                    books.append(b)
                    continue
            b = self._read(f, st, added=c[2] if c else st.st_mtime)
            books.append(b)
            self.conn.execute(
                "INSERT OR REPLACE INTO books VALUES (?,?,?,?,?)",
                (f, st.st_mtime, st.st_size, b.added, json.dumps(b.__dict__)),
            )
            if progress:
                progress(n + 1, len(files))
        # Gelöschte Dateien aus dem Cache werfen
        for gone in set(cached) - seen:
            self.conn.execute("DELETE FROM books WHERE path=?", (gone,))
        self.conn.commit()
        return books

    def _read(self, f: str, st, added: float) -> Book:
        try:
            m = read_epub(f)
        except Exception:  # kaputte EPUBs trotzdem anzeigen
            return Book(path=f, title=Path(f).stem, size=st.st_size, mtime=st.st_mtime, added=added)
        cover_path = ""
        if m.cover:
            img = QImage.fromData(m.cover)
            if not img.isNull():
                img = img.scaled(400, 600, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                key = hashlib.sha1(f.encode()).hexdigest()
                cover_path = str(cover_dir() / f"{key}.jpg")
                img.save(cover_path, "JPG", 88)
        return Book(
            path=f,
            title=m.title or Path(f).stem,
            authors=m.authors,
            language=m.language,
            description=m.description,
            publisher=m.publisher,
            date=m.date,
            series=m.series,
            series_index=m.series_index,
            subjects=m.subjects,
            isbn=m.isbn,
            cover_path=cover_path,
            size=st.st_size,
            mtime=st.st_mtime,
            added=added,
        )


def safe_filename(s: str) -> str:
    bad = '<>:"/\\|?*\0'
    s = "".join("_" if ch in bad else ch for ch in s).strip(" .")
    return s[:150] or "Buch"


def import_epub(src: str, library_dir: str) -> str:
    """EPUB in die Bibliothek kopieren, sinnvoll benannt als 'Autor - Titel.epub'."""
    try:
        m = read_epub(src, with_cover=False)
        author = m.authors[0] if m.authors else "Unbekannt"
        name = safe_filename(f"{author} - {m.title or Path(src).stem}")
    except Exception:
        name = safe_filename(Path(src).stem)
    dest = Path(library_dir) / f"{name}.epub"
    i = 2
    while dest.exists():
        if os.path.samefile(dest, src):
            return str(dest)
        dest = Path(library_dir) / f"{name} ({i}).epub"
        i += 1
    shutil.copy2(src, dest)
    return str(dest)
