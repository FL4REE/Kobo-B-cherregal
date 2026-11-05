"""Beobachtet einen Ordner (Standard ~/Downloads) und meldet neue, fertig geladene EPUBs."""
from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QFileSystemWatcher, QObject, QTimer, Signal


def default_downloads_dir() -> str:
    # XDG-Downloadordner auslesen (bei deutschem System oft auch ~/Downloads)
    cfg = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "user-dirs.dirs"
    try:
        for line in cfg.read_text().splitlines():
            if line.startswith("XDG_DOWNLOAD_DIR="):
                v = line.split("=", 1)[1].strip().strip('"').replace("$HOME", str(Path.home()))
                if Path(v).is_dir():
                    return v
    except OSError:
        pass
    return str(Path.home() / "Downloads")


class DownloadWatcher(QObject):
    """Sendet new_epub(path), sobald eine neue .epub-Datei fertig im Ordner liegt.

    Dateien, die beim Einschalten schon da sind, werden ignoriert. Eine Datei gilt als
    fertig, wenn ihre Größe zwei Prüfungen lang gleich bleibt (Browser schreiben erst
    in .part/.crdownload und benennen am Ende um)."""

    new_epub = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.folder = ""
        self.fs = QFileSystemWatcher(self)
        self.fs.directoryChanged.connect(lambda _: self.timer.start())
        self.timer = QTimer(self, singleShot=True, interval=1500, timeout=self._check)
        self.known: set[str] = set()
        self.pending: dict[str, int] = {}

    def _epubs(self) -> list[str]:
        try:
            return [e.path for e in os.scandir(self.folder)
                    if e.is_file() and e.name.lower().endswith(".epub") and not e.name.startswith(".")]
        except OSError:
            return []

    def start(self, folder: str):
        self.stop()
        if not Path(folder).is_dir():
            return False
        self.folder = folder
        self.known = set(self._epubs())
        self.fs.addPath(folder)
        return True

    def stop(self):
        if self.fs.directories():
            self.fs.removePaths(self.fs.directories())
        self.folder = ""
        self.pending.clear()
        self.timer.stop()

    @property
    def active(self) -> bool:
        return bool(self.folder)

    def _check(self):
        if not self.folder:
            return
        current = set(self._epubs())
        self.known &= current  # gelöschte Dateien vergessen
        again = False
        for p in current - self.known:
            try:
                size = os.path.getsize(p)
            except OSError:
                continue
            if size > 0 and self.pending.get(p) == size:
                self.pending.pop(p, None)
                self.known.add(p)
                self.new_epub.emit(p)
            else:
                self.pending[p] = size
                again = True
        if again:
            self.timer.start()
