"""Hauptfenster mit Bibliothek, Entdecken (legale Quellen), Kobo und Einstellungen."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QSettings, QSize, Qt, QThreadPool, QTimer, QUrl
from PySide6.QtGui import QAction, QColor, QDesktopServices, QIcon, QKeySequence, QPixmap
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkRequest
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QButtonGroup, QCheckBox, QComboBox,
                               QFileDialog, QFrame, QHBoxLayout, QHeaderView, QInputDialog, QLabel,
                               QLineEdit, QListView, QListWidget, QListWidgetItem, QMainWindow,
                               QMenu, QMessageBox, QProgressBar, QPushButton, QScrollArea,
                               QStackedWidget, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout, QWidget)

from . import kobo, style
from .watcher import DownloadWatcher, default_downloads_dir
from .library import Book, LibraryDB, default_library_dir, import_epub, safe_filename
from .epub import read_epub
from .sources import GutenbergSource, InternetArchiveSource, OPDSSource, RemoteBook, WikisourceSource, download
from .widgets import (BookDelegate, BookListModel, BookRole, LibraryFilter, Worker, COVERS,
                      placeholder_cover, rounded)

APP_NAME = "Bücherregal"
POOL = QThreadPool.globalInstance()


_RUNNING: set = set()  # Referenzen halten, sonst sammelt Python die Signale ein, bevor sie ankommen


def run_bg(fn, *args, done=None, error=None, progress=None, **kw):
    w = Worker(fn, *args, with_progress=progress is not None, **kw)
    w.setAutoDelete(False)
    _RUNNING.add(w)
    w.signals.done.connect(lambda _: _RUNNING.discard(w))
    w.signals.error.connect(lambda _: _RUNNING.discard(w))
    if done:
        w.signals.done.connect(done)
    if error:
        w.signals.error.connect(error)
    if progress:
        w.signals.progress.connect(progress)
    POOL.start(w)
    return w


def human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024
    return ""


LANG_NAMES = {"de": "Deutsch", "en": "Englisch", "fr": "Französisch", "es": "Spanisch",
              "it": "Italienisch", "nl": "Niederländisch"}


def lang_name(code: str) -> str:
    c = (code or "").split("-")[0]
    return LANG_NAMES.get(c, c.upper() or "Unbekannt")


def label(text="", obj=None, wrap=False) -> QLabel:
    l = QLabel(text)
    if obj:
        l.setObjectName(obj)
    l.setWordWrap(wrap)
    return l


def button(text, obj=None, slot=None) -> QPushButton:
    b = QPushButton(text)
    if obj:
        b.setObjectName(obj)
    b.setCursor(Qt.PointingHandCursor)
    if slot:
        b.clicked.connect(slot)
    return b


class FlowChips(QWidget):
    """Einfache Zeile mit umbrechenden Chips."""

    def __init__(self):
        super().__init__()
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(6)

    def set_chips(self, chips: list[tuple[str, bool]], per_row=3):
        while self.lay.count():
            it = self.lay.takeAt(0)
            if it.layout():
                while it.layout().count():
                    w = it.layout().takeAt(0).widget()
                    if w:
                        w.hide()
                        w.deleteLater()
        row = None
        for i, (text, accent) in enumerate(chips):
            if i % per_row == 0:
                row = QHBoxLayout()
                row.setSpacing(6)
                self.lay.addLayout(row)
            row.addWidget(label(text, "chipAccent" if accent else "chip"))
        if row:
            row.addStretch()


# ============================================================== Detailansicht

class DetailPanel(QFrame):
    def __init__(self, remote=False):
        super().__init__()
        self.setObjectName("detail")
        self.setFixedWidth(370)
        self.remote = remote
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        outer.addWidget(scroll)
        body = QWidget()
        scroll.setWidget(body)
        lay = QVBoxLayout(body)
        lay.setContentsMargins(26, 26, 26, 26)
        lay.setSpacing(12)

        self.cover = QLabel()
        self.cover.setAlignment(Qt.AlignCenter)
        self.cover.setFixedHeight(330)
        lay.addWidget(self.cover)
        self.title = label("", "h2", wrap=True)
        self.title.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.title)
        self.author = label("", "muted", wrap=True)
        lay.addWidget(self.author)
        self.chips = FlowChips()
        lay.addWidget(self.chips)

        self.actions = QVBoxLayout()
        self.actions.setSpacing(8)
        lay.addLayout(self.actions)
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        lay.addWidget(self.progress)

        self.desc_head = label("Beschreibung", "muted")
        lay.addWidget(self.desc_head)
        self.desc = label("", wrap=True)
        self.desc.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.desc.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        lay.addWidget(self.desc)
        self.meta = label("", "muted", wrap=True)
        self.meta.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.meta)
        lay.addStretch()

    def clear_actions(self):
        while self.actions.count():
            it = self.actions.takeAt(0)
            if it.widget():
                it.widget().hide()
                it.widget().deleteLater()
            elif it.layout():
                while it.layout().count():
                    w = it.layout().takeAt(0).widget()
                    if w:
                        w.hide()
                        w.deleteLater()

    def set_cover(self, pm: QPixmap | None, title: str, author: str):
        w, h = 220, 330
        if pm is None or pm.isNull():
            pm = placeholder_cover(title, author, w, h)
        else:
            pm = pm.scaled(w, h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.cover.setPixmap(rounded(pm, pm.width(), pm.height(), 10))

    def show_book(self, b, cover: QPixmap | None, chips, meta_lines, buttons):
        self.set_cover(cover, b.title, b.author_str)
        self.title.setText(b.title)
        self.author.setText(b.author_str)
        self.chips.set_chips(chips)
        self.desc.setText(b.description or "Keine Beschreibung vorhanden.")
        self.meta.setText("<br>".join(meta_lines))
        self.meta.setVisible(bool(meta_lines))
        self.clear_actions()
        row = None
        for i, btn in enumerate(buttons):
            if i == 0:
                self.actions.addWidget(btn)
            else:
                if row is None or row.count() >= 2:
                    row = QHBoxLayout()
                    row.setSpacing(8)
                    self.actions.addLayout(row)
                row.addWidget(btn)
        self.progress.setVisible(False)


class EmptyState(QWidget):
    def __init__(self, title, text, btn_text=None, slot=None):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.addStretch()
        icon = label("📚")
        icon.setAlignment(Qt.AlignCenter)
        icon.setStyleSheet("font-size: 42pt;")
        lay.addWidget(icon)
        t = label(title, "h2")
        t.setAlignment(Qt.AlignCenter)
        lay.addWidget(t)
        self.text = label(text, "muted", wrap=True)
        self.text.setAlignment(Qt.AlignCenter)
        self.text.setMaximumWidth(460)
        lay.addWidget(self.text, 0, Qt.AlignHCenter)
        if btn_text:
            lay.addSpacing(8)
            lay.addWidget(button(btn_text, "primary", slot), 0, Qt.AlignHCenter)
        lay.addStretch()


def make_grid(delegate) -> QListView:
    v = QListView()
    v.setViewMode(QListView.IconMode)
    v.setResizeMode(QListView.Adjust)
    v.setMovement(QListView.Static)
    v.setUniformItemSizes(True)
    v.setSpacing(4)
    v.setMouseTracking(True)
    v.setSelectionMode(QAbstractItemView.ExtendedSelection)
    v.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
    v.verticalScrollBar().setSingleStep(24)
    v.setItemDelegate(delegate)
    v.setContextMenuPolicy(Qt.CustomContextMenu)
    return v


# ============================================================== Bibliothek

class LibraryPage(QWidget):
    def __init__(self, win: "MainWindow"):
        super().__init__()
        self.win = win
        self.setObjectName("page")
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        main = QVBoxLayout()
        main.setContentsMargins(28, 24, 18, 12)
        main.setSpacing(14)
        head = QHBoxLayout()
        self.h1 = label("Bibliothek", "h1")
        head.addWidget(self.h1)
        self.count = label("", "muted")
        head.addWidget(self.count, 0, Qt.AlignBottom)
        head.addStretch()
        head.addWidget(button("＋  EPUBs importieren", "primary", self.import_dialog))
        main.addLayout(head)

        bar = QHBoxLayout()
        bar.setSpacing(10)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Suchen nach Titel, Autor, Serie …")
        self.search.setClearButtonEnabled(True)
        bar.addWidget(self.search, 1)
        self.sort = QComboBox()
        self.sort.addItems(LibraryFilter.SORTS)
        bar.addWidget(self.sort)
        self.german = QCheckBox("Nur deutsche Bücher")
        bar.addWidget(self.german)
        bar.addWidget(button("⟳", "ghost", lambda: self.win.rescan()))
        main.addLayout(bar)

        self.scanbar = QProgressBar()
        self.scanbar.setVisible(False)
        main.addWidget(self.scanbar)

        self.model = BookListModel()
        self.proxy = LibraryFilter()
        self.proxy.setSourceModel(self.model)
        self.grid = make_grid(BookDelegate(badge_fn=self.badges))
        self.grid.setModel(self.proxy)
        self.stack = QStackedWidget()
        self.empty = EmptyState(
            "Noch keine Bücher hier",
            "Zieh EPUB-Dateien einfach ins Fenster, importier sie über den Button oder hol dir "
            "gemeinfreie Klassiker unter „Entdecken“.",
            "Klassiker entdecken", lambda: self.win.go(1))
        self.noresult = EmptyState("Nichts gefunden", "Versuch es mit einem anderen Suchbegriff.")
        self.stack.addWidget(self.grid)
        self.stack.addWidget(self.empty)
        self.stack.addWidget(self.noresult)
        main.addWidget(self.stack, 1)
        root.addLayout(main, 1)

        self.detail = DetailPanel()
        self.detail.setVisible(False)
        root.addWidget(self.detail)

        s = win.settings
        self.german.setChecked(s.value("german_only", False, bool))
        self.sort.setCurrentIndex(int(s.value("sort", 0)))
        self.proxy.set_german_only(self.german.isChecked())
        self.proxy.set_sort(self.sort.currentIndex())

        self.search.textChanged.connect(self._filter)
        self.german.toggled.connect(self._german)
        self.sort.currentIndexChanged.connect(self._sort)
        self.grid.selectionModel().selectionChanged.connect(self._selected)
        self.grid.doubleClicked.connect(lambda i: self.open_book(i.data(BookRole)))
        self.grid.customContextMenuRequested.connect(self._menu)

    # --- Daten
    def set_books(self, books: list[Book]):
        sel = self.current()
        self.model.set_items(books)
        self.proxy.set_sort(self.sort.currentIndex())
        self._update_state()
        if sel:
            for r in range(self.proxy.rowCount()):
                if self.proxy.index(r, 0).data(BookRole).path == sel.path:
                    self.grid.setCurrentIndex(self.proxy.index(r, 0))
                    break

    def badges(self, b: Book):
        out = []
        if self.win.on_kobo(b):
            out.append(("✓ Kobo", style.GOOD))
        if b.language and not b.is_german:
            out.append((b.language.split("-")[0].upper(), style.ACCENT))
        return out

    def _update_state(self):
        n, total = self.proxy.rowCount(), self.model.rowCount()
        self.count.setText(f"{total} Bücher" if n == total else f"{n} von {total} Büchern")
        self.stack.setCurrentIndex(1 if total == 0 else 2 if n == 0 else 0)

    def _filter(self, t):
        self.proxy.set_text(t)
        self._update_state()

    def _german(self, v):
        self.win.settings.setValue("german_only", v)
        self.proxy.set_german_only(v)
        self._update_state()

    def _sort(self, i):
        self.win.settings.setValue("sort", i)
        self.proxy.set_sort(i)

    def current(self) -> Book | None:
        idx = self.grid.currentIndex()
        return idx.data(BookRole) if idx.isValid() else None

    def selected_books(self) -> list[Book]:
        return [i.data(BookRole) for i in self.grid.selectionModel().selectedIndexes()]

    def refresh_detail(self):
        self._selected()
        self.grid.viewport().update()

    def _selected(self, *_):
        books = self.selected_books()
        if not books:
            self.detail.setVisible(False)
            return
        b = self.current() if self.current() in books else books[0]
        self.detail.setVisible(True)
        chips = []
        if b.series:
            chips.append((b.series_str, True))
        chips.append((lang_name(b.language), False))
        if b.date[:4].isdigit():
            chips.append((b.date[:4], False))
        chips.append((human_size(b.size), False))
        if self.win.on_kobo(b):
            chips.append(("✓ auf dem Kobo", True))
        meta = []
        if b.publisher:
            meta.append(f"Verlag: {b.publisher}")
        if b.isbn:
            meta.append(f"ISBN: {b.isbn}")
        if b.subjects:
            meta.append("Genres: " + ", ".join(b.subjects[:6]))
        meta.append(f"Hinzugefügt: {datetime.fromtimestamp(b.added):%d.%m.%Y}")
        meta.append(f"<span style='font-size:8pt'>{Path(b.path).name}</span>")

        n = len(books)
        kobo_btn = button(f"⇪  {n} Bücher auf den Kobo" if n > 1 else "⇪  Auf den Kobo übertragen",
                          "primary", lambda: self.win.send_to_kobo(self.selected_books()))
        kobo_btn.setEnabled(self.win.kobo_mount is not None)
        if self.win.kobo_mount is None:
            kobo_btn.setToolTip("Schließ deinen Kobo per USB an und tippe auf „Verbinden“.")
        btns = [kobo_btn,
                button("Lesen", None, lambda: self.open_book(b)),
                button("Im Ordner zeigen", None, lambda: self.reveal(b)),
                button("Löschen", "danger", lambda: self.delete(self.selected_books()))]
        self.detail.show_book(b, QPixmap(b.cover_path) if b.cover_path else None, chips, meta, btns)

    # --- Aktionen
    def open_book(self, b: Book | None):
        if b:
            QDesktopServices.openUrl(QUrl.fromLocalFile(b.path))

    def reveal(self, b: Book):
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(b.path).parent)))

    def delete(self, books: list[Book]):
        if not books:
            return
        what = f"„{books[0].title}“" if len(books) == 1 else f"{len(books)} Bücher"
        r = QMessageBox.question(self, "Löschen", f"{what} in den Papierkorb verschieben?")
        if r != QMessageBox.Yes:
            return
        from PySide6.QtCore import QFile
        failed = [b.title for b in books if not QFile.moveToTrash(b.path)]
        if failed:
            QMessageBox.warning(self, "Löschen", "Konnte nicht löschen:\n" + "\n".join(failed))
        self.win.rescan()

    def import_dialog(self):
        files, _ = QFileDialog.getOpenFileNames(self, "EPUBs importieren", str(Path.home() / "Downloads"),
                                                "E-Books (*.epub)")
        if files:
            self.win.import_files(files)

    def _menu(self, pos):
        if not self.grid.indexAt(pos).isValid():
            return
        books = self.selected_books()
        m = QMenu(self)
        a = m.addAction("Auf den Kobo übertragen", lambda: self.win.send_to_kobo(books))
        a.setEnabled(self.win.kobo_mount is not None)
        if len(books) == 1:
            m.addAction("Lesen", lambda: self.open_book(books[0]))
            m.addAction("Im Ordner zeigen", lambda: self.reveal(books[0]))
        m.addSeparator()
        m.addAction("In den Papierkorb", lambda: self.delete(books))
        m.exec(self.grid.viewport().mapToGlobal(pos))


# ============================================================== Entdecken

class DiscoverPage(QWidget):
    def __init__(self, win: "MainWindow"):
        super().__init__()
        self.win = win
        self.setObjectName("page")
        self.net = QNetworkAccessManager(self)
        self.page = 1
        self.query = ""
        self.busy = False
        self.pending_covers: set[str] = set()

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        main = QVBoxLayout()
        main.setContentsMargins(28, 24, 18, 12)
        main.setSpacing(14)
        main.addWidget(label("Entdecken", "h1"))
        main.addWidget(label("Gemeinfreie deutsche Klassiker als EPUB, kostenlos und legal. "
                             "Fertige Downloads landen direkt in deiner Bibliothek.", "muted", wrap=True))

        bar = QHBoxLayout()
        bar.setSpacing(10)
        self.source = QComboBox()
        bar.addWidget(self.source)
        self.search = QLineEdit()
        self.search.setPlaceholderText("z.B. Kafka, Fontane, Effi Briest, Märchen …")
        self.search.setClearButtonEnabled(True)
        self.search.returnPressed.connect(self.do_search)
        bar.addWidget(self.search, 1)
        bar.addWidget(button("Suchen", "primary", self.do_search))
        main.addLayout(bar)

        self.status = label("", "muted")
        main.addWidget(self.status)

        self.model = BookListModel()
        self.grid = make_grid(BookDelegate(badge_fn=self.badges))
        self.grid.setSelectionMode(QAbstractItemView.SingleSelection)
        self.grid.setModel(self.model)
        self.grid.selectionModel().currentChanged.connect(lambda *_: self._selected())
        self.grid.verticalScrollBar().valueChanged.connect(self._maybe_more)
        main.addWidget(self.grid, 1)
        root.addLayout(main, 1)

        self.detail = DetailPanel(remote=True)
        self.detail.setVisible(False)
        root.addWidget(self.detail)
        self.reload_sources()
        self.source.currentIndexChanged.connect(lambda _: self.do_search())
        self.has_more = False
        self.downloading: set[str] = set()

    def reload_sources(self):
        self.sources = [GutenbergSource(), WikisourceSource(), InternetArchiveSource()]
        for f in self.win.opds_feeds():
            self.sources.append(OPDSSource(f["name"], f["url"]))
        self.source.blockSignals(True)
        cur = self.source.currentText()
        self.source.clear()
        self.source.addItems([s.name for s in self.sources])
        i = self.source.findText(cur)
        self.source.setCurrentIndex(max(0, i))
        self.source.blockSignals(False)

    def showEvent(self, e):
        super().showEvent(e)
        if not self.model.items and not self.busy:
            self.do_search()

    def badges(self, rb: RemoteBook):
        return [("✓ Bibliothek", style.GOOD)] if self.win.in_library(rb) else []

    def do_search(self):
        self.query = self.search.text()
        self.page = 1
        self.model.set_items([])
        self.detail.setVisible(False)
        self._fetch()

    def _fetch(self):
        src = self.sources[self.source.currentIndex()]
        if isinstance(src, WikisourceSource) and not self.query.strip():
            self.status.setText("Gib einen Titel oder Autor ein, um Wikisource zu durchsuchen.")
            return
        self.busy = True
        self.status.setText("Suche läuft …" if self.page == 1 else "Lade mehr …")
        run_bg(src.search, self.query, self.page, done=self._results, error=self._error)

    def _results(self, res):
        items, more = res
        self.busy = False
        self.has_more = more
        self.model.append(items)
        n = self.model.rowCount()
        self.status.setText(f"{n} Treffer" + (" (scroll für mehr)" if more else "") if n else
                            "Keine deutschen EPUBs gefunden.")
        for rb in items:
            if rb.cover_url:
                self._load_cover(rb)

    def _error(self, msg):
        self.busy = False
        self.status.setText(f"⚠ Quelle nicht erreichbar: {msg}")

    def _maybe_more(self, v):
        sb = self.grid.verticalScrollBar()
        if self.has_more and not self.busy and v > sb.maximum() - 300:
            self.page += 1
            self._fetch()

    def _load_cover(self, rb: RemoteBook):
        key = self.model.key(rb)
        if key in self.pending_covers or key in self.model.pixmaps:
            return
        self.pending_covers.add(key)
        req = QNetworkRequest(QUrl(rb.cover_url))
        req.setAttribute(QNetworkRequest.RedirectPolicyAttribute,
                         QNetworkRequest.NoLessSafeRedirectPolicy)
        reply = self.net.get(req)

        def fin():
            pm = QPixmap()
            if pm.loadFromData(reply.readAll()) and not pm.isNull():
                self.model.set_pixmap(key, pm)
                cur = self._current()
                if cur and self.model.key(cur) == key:
                    self._selected()
            reply.deleteLater()
        reply.finished.connect(fin)

    def _current(self) -> RemoteBook | None:
        i = self.grid.currentIndex()
        return i.data(BookRole) if i.isValid() else None

    def _selected(self):
        rb = self._current()
        if not rb:
            return
        self.detail.setVisible(True)
        chips = [(rb.source, True), (lang_name(rb.language), False), ("EPUB", False)]
        meta = [rb.extra] if rb.extra else []
        have = self.win.in_library(rb)
        key = self.model.key(rb)
        if key in self.downloading:
            dl = button("Wird geladen …", "primary")
            dl.setEnabled(False)
        elif have:
            dl = button("✓ In deiner Bibliothek", "primary", lambda: self.win.show_in_library(have))
        else:
            dl = button("⤓  Herunterladen", "primary", lambda: self.download(rb))
        btns = [dl]
        if isinstance(self.sources[self.source.currentIndex()], GutenbergSource):
            btns.append(button("Auf gutenberg.org", None, lambda: QDesktopServices.openUrl(
                QUrl(f"https://www.gutenberg.org/ebooks/{rb.id}"))))
        elif isinstance(self.sources[self.source.currentIndex()], InternetArchiveSource):
            btns.append(button("Auf archive.org", None, lambda: QDesktopServices.openUrl(
                QUrl(f"https://archive.org/details/{rb.id}"))))
        self.detail.show_book(rb, self.model.pixmaps.get(key), chips, meta, btns)
        if key in self.downloading:
            self.detail.progress.setVisible(True)

    def download(self, rb: RemoteBook):
        key = self.model.key(rb)
        self.downloading.add(key)
        self._selected()
        lib = self.win.library_dir()
        author = rb.authors[0] if rb.authors else "Unbekannt"
        tmp = tempfile.NamedTemporaryFile(suffix=".epub", delete=False, dir=lib, prefix=".dl-")
        tmp.close()
        final = Path(lib) / f"{safe_filename(f'{author} - {rb.title}')}.epub"

        def job(progress=None):
            download(rb.epub_url, tmp.name, progress=progress)
            dest = final
            i = 2
            while dest.exists():
                dest = final.with_name(f"{final.stem} ({i}).epub")
                i += 1
            os.replace(tmp.name, dest)
            return str(dest)

        def prog(a, b):
            cur = self._current()
            if cur and self.model.key(cur) == key:
                self.detail.progress.setVisible(True)
                self.detail.progress.setMaximum(b or 0)
                self.detail.progress.setValue(a)

        def ok(path):
            self.downloading.discard(key)
            self.win.toast(f"„{rb.title}“ ist jetzt in deiner Bibliothek.")
            self.win.rescan(after=lambda: (self._selected(), self.grid.viewport().update()))

        def fail(msg):
            self.downloading.discard(key)
            try:
                os.unlink(tmp.name)
            except OSError:
                pass
            QMessageBox.warning(self, "Download fehlgeschlagen", msg)
            self._selected()

        run_bg(job, done=ok, error=fail, progress=prog)


# ============================================================== Kobo

class KoboPage(QWidget):
    def __init__(self, win: "MainWindow"):
        super().__init__()
        self.win = win
        self.setObjectName("page")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 16)
        lay.setSpacing(14)
        lay.addWidget(label("Kobo", "h1"))

        card = QFrame()
        card.setObjectName("card")
        cl = QHBoxLayout(card)
        cl.setContentsMargins(20, 16, 20, 16)
        self.icon = label("📖")
        self.icon.setStyleSheet("font-size: 30pt;")
        cl.addWidget(self.icon)
        txt = QVBoxLayout()
        self.state = label("", "h2")
        self.sub = label("", "muted", wrap=True)
        txt.addWidget(self.state)
        txt.addWidget(self.sub)
        self.space = QProgressBar()
        self.space.setMaximumWidth(320)
        txt.addWidget(self.space)
        cl.addLayout(txt, 1)
        self.eject_btn = button("Auswerfen", None, self.eject)
        cl.addWidget(self.eject_btn, 0, Qt.AlignVCenter)
        lay.addWidget(card)

        opts = QHBoxLayout()
        self.kepub = QCheckBox("Als KEPUB übertragen (schneller und mit Lesestatistik, braucht kepubify)")
        self.kepub.setChecked(win.settings.value("kepub", True, bool))
        self.kepub.toggled.connect(lambda v: win.settings.setValue("kepub", v))
        opts.addWidget(self.kepub)
        opts.addStretch()
        lay.addLayout(opts)

        bar = QHBoxLayout()
        self.only_new = QCheckBox("Nur Bücher zeigen, die noch nicht auf dem Kobo sind")
        self.only_new.toggled.connect(lambda _: self.refresh())
        bar.addWidget(self.only_new)
        bar.addStretch()
        self.send_sel = button("Ausgewählte übertragen", None, self.send_selected)
        self.send_new = button("Alle neuen übertragen", "primary", self.send_all_new)
        bar.addWidget(self.send_sel)
        bar.addWidget(self.send_new)
        lay.addLayout(bar)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Titel", "Autor", "Serie", "Auf dem Kobo"])
        self.tree.setRootIsDecorated(False)
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tree.setIconSize(QSize(28, 42))
        self.tree.setSortingEnabled(True)
        h = self.tree.header()
        h.setSectionResizeMode(0, QHeaderView.Stretch)
        h.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        lay.addWidget(self.tree, 1)
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        lay.addWidget(self.progress)

    def refresh(self):
        m = self.win.kobo_mount
        if m:
            info = kobo.kobo_info(m)
            self.state.setText("Kobo verbunden")
            fw = f" · Firmware {info['firmware']}" if info.get("firmware") else ""
            self.sub.setText(f"{info['mount']}{fw} · {human_size(info['free'])} frei von "
                             f"{human_size(info['total'])}")
            self.space.setVisible(True)
            self.space.setMaximum(1000)
            self.space.setValue(int(1000 * (1 - info["free"] / info["total"])) if info["total"] else 0)
        else:
            self.state.setText("Kein Kobo gefunden")
            self.sub.setText("Schließ deinen Kobo per USB an und tippe auf dem Reader auf „Verbinden“. "
                             "Er taucht hier automatisch auf.")
            self.space.setVisible(False)
        if not kobo.kepubify_available():
            self.kepub.setText("Als KEPUB übertragen (kepubify ist nicht installiert, z.B. `yay -S kepubify-bin`)")
            self.kepub.blockSignals(True)
            self.kepub.setChecked(False)
            self.kepub.blockSignals(False)
            self.kepub.setEnabled(False)
        for w in (self.eject_btn, self.send_sel, self.send_new):
            w.setEnabled(m is not None)

        self.tree.setSortingEnabled(False)
        self.tree.clear()
        for b in self.win.books:
            on = self.win.on_kobo(b)
            if self.only_new.isChecked() and on:
                continue
            it = QTreeWidgetItem([b.title, b.author_str, b.series_str, "✓" if on else ""])
            it.setData(0, Qt.UserRole, b.path)
            it.setIcon(0, QIcon(COVERS.get(b.path + "#mini", b.title, b.author_str, 28, 42,
                                           path=b.cover_path)))
            if on:
                it.setForeground(3, QColor(style.GOOD))
            self.tree.addTopLevelItem(it)
        self.tree.setSortingEnabled(True)

    def _books(self, paths):
        by = {b.path: b for b in self.win.books}
        return [by[p] for p in paths if p in by]

    def send_selected(self):
        paths = [i.data(0, Qt.UserRole) for i in self.tree.selectedItems()]
        if not paths:
            self.win.toast("Wähl erst ein paar Bücher in der Liste aus.")
            return
        self.win.send_to_kobo(self._books(paths))

    def send_all_new(self):
        books = [b for b in self.win.books if not self.win.on_kobo(b)]
        if not books:
            self.win.toast("Alles schon auf dem Kobo 👍")
            return
        r = QMessageBox.question(self, "Übertragen", f"{len(books)} neue Bücher auf den Kobo übertragen?")
        if r == QMessageBox.Yes:
            self.win.send_to_kobo(books)

    def eject(self):
        if not self.win.kobo_mount:
            return
        ok, msg = kobo.eject(self.win.kobo_mount)
        if ok:
            # Falls der Kobo nur ausgehängt, aber nicht abgeschaltet wurde: nicht sofort wieder einhängen
            dev = kobo.find_unmounted_kobo()
            if dev:
                self.win._mount_failed.add(dev)
        (self.win.toast if ok else lambda m: QMessageBox.warning(self, "Auswerfen", m))(msg)
        if ok:
            self.win.check_kobo(force=True)


# ============================================================== Einstellungen

class SettingsPage(QWidget):
    def __init__(self, win: "MainWindow"):
        super().__init__()
        self.win = win
        self.setObjectName("page")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 16)
        lay.setSpacing(12)
        lay.addWidget(label("Einstellungen", "h1"))

        lay.addWidget(label("Bibliotheksordner", "h2"))
        lay.addWidget(label("Hier liegen deine EPUBs. Unterordner werden mitgescannt.", "muted"))
        row = QHBoxLayout()
        self.dir = QLineEdit(win.library_dir())
        self.dir.setReadOnly(True)
        row.addWidget(self.dir, 1)
        row.addWidget(button("Ändern …", None, self.pick_dir))
        row.addWidget(button("Öffnen", None, lambda: QDesktopServices.openUrl(
            QUrl.fromLocalFile(win.library_dir()))))
        lay.addLayout(row)

        lay.addSpacing(14)
        lay.addWidget(label("Eigene OPDS-Kataloge", "h2"))
        lay.addWidget(label("Zusätzliche legale Quellen, z.B. dein eigener Calibre-Server oder ein "
                            "OPDS-Katalog deiner Bibliothek. Es werden nur deutsche EPUBs angezeigt.",
                            "muted", wrap=True))
        self.feeds = QListWidget()
        self.feeds.setObjectName("plain")
        self.feeds.setMaximumHeight(180)
        lay.addWidget(self.feeds)
        row2 = QHBoxLayout()
        row2.addWidget(button("＋ Katalog hinzufügen", None, self.add_feed))
        row2.addWidget(button("Entfernen", "danger", self.remove_feed))
        row2.addStretch()
        lay.addLayout(row2)

        lay.addSpacing(14)
        lay.addWidget(label("Downloads beobachten", "h2"))
        lay.addWidget(label("Neue EPUBs, die in diesem Ordner landen (z.B. gekaufte DRM-freie Bücher), "
                            "kommen automatisch in deine Bibliothek. Doppelte werden übersprungen.",
                            "muted", wrap=True))
        s = win.settings
        self.watch = QCheckBox("Ordner beobachten")
        self.watch.setChecked(s.value("watch_downloads", True, bool))
        lay.addWidget(self.watch)
        row3 = QHBoxLayout()
        self.watch_dir = QLineEdit(win.downloads_dir())
        self.watch_dir.setReadOnly(True)
        row3.addWidget(self.watch_dir, 1)
        row3.addWidget(button("Ändern …", None, self.pick_watch_dir))
        row3.addWidget(button("Jetzt alle importieren", None, self.import_all_downloads))
        lay.addLayout(row3)
        self.trash_orig = QCheckBox("Original nach dem Import in den Papierkorb verschieben")
        self.trash_orig.setChecked(s.value("watch_trash", False, bool))
        self.trash_orig.toggled.connect(lambda v: s.setValue("watch_trash", v))
        lay.addWidget(self.trash_orig)
        self.watch.toggled.connect(self._watch_toggled)

        lay.addSpacing(14)
        lay.addWidget(label("Lese-App", "h2"))
        lay.addWidget(label("„Lesen“ öffnet das EPUB mit deinem Standardprogramm. Gut auf Linux: "
                            "Foliate (Flatpak `com.github.johnfactotum.Foliate` oder `pacman -S foliate`).", "muted", wrap=True))
        lay.addStretch()
        self.load_feeds()

    def _watch_toggled(self, v):
        self.win.settings.setValue("watch_downloads", v)
        self.win.apply_watch()

    def pick_watch_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Ordner zum Beobachten", self.win.downloads_dir())
        if d:
            self.win.settings.setValue("downloads_dir", d)
            self.watch_dir.setText(d)
            self.win.apply_watch()

    def import_all_downloads(self):
        d = self.win.downloads_dir()
        files = [str(p) for p in Path(d).glob("*") if p.suffix.lower() == ".epub" and p.is_file()]
        if not files:
            self.win.toast(f"Keine EPUBs in {d} gefunden.")
            return
        for f in files:
            self.win.import_from_downloads(f)

    def load_feeds(self):
        self.feeds.clear()
        for f in self.win.opds_feeds():
            it = QListWidgetItem(f"{f['name']}   ·   {f['url']}")
            self.feeds.addItem(it)

    def pick_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Bibliotheksordner wählen", self.win.library_dir())
        if d:
            self.win.settings.setValue("library_dir", d)
            self.dir.setText(d)
            self.win.rescan()

    def add_feed(self):
        url, ok = QInputDialog.getText(self, "OPDS-Katalog", "Adresse des Katalogs (https://…):")
        if not ok or not url.strip().startswith(("http://", "https://")):
            return
        name, ok = QInputDialog.getText(self, "OPDS-Katalog", "Name für die Quelle:")
        if not ok or not name.strip():
            return
        feeds = self.win.opds_feeds() + [{"name": name.strip(), "url": url.strip()}]
        self.win.settings.setValue("opds_feeds", json.dumps(feeds))
        self.load_feeds()
        self.win.discover.reload_sources()

    def remove_feed(self):
        r = self.feeds.currentRow()
        if r < 0:
            return
        feeds = self.win.opds_feeds()
        del feeds[r]
        self.win.settings.setValue("opds_feeds", json.dumps(feeds))
        self.load_feeds()
        self.win.discover.reload_sources()


# ============================================================== Hauptfenster

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1360, 860)
        self.setAcceptDrops(True)
        self.settings = QSettings("buecherregal", "buecherregal")
        self.db = LibraryDB()
        self.books: list[Book] = []
        self.kobo_mount: Path | None = None
        self.kobo_names: set[str] = set()
        self._mount_failed: set[str] = set()
        self._scanning = False
        self._after_scan = []

        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        h = QHBoxLayout(root)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)

        side = QFrame()
        side.setObjectName("sidebar")
        side.setFixedWidth(210)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(14, 22, 14, 16)
        sl.setSpacing(4)
        sl.addWidget(label("📚 " + APP_NAME, "logo"))
        self.nav = QButtonGroup(self)
        for i, name in enumerate(["  Bibliothek", "  Entdecken", "  Kobo", "  Einstellungen"]):
            b = button(["▦", "✦", "⇪", "⚙"][i] + name, "nav")
            b.setCheckable(True)
            self.nav.addButton(b, i)
            sl.addWidget(b)
        sl.addStretch()
        self.kobo_pill = label("", "koboPill", wrap=True)
        self.kobo_pill.setCursor(Qt.PointingHandCursor)
        self.kobo_pill.mousePressEvent = lambda e: self.go(2)
        sl.addWidget(self.kobo_pill)
        h.addWidget(side)

        self.pages = QStackedWidget()
        self.library = LibraryPage(self)
        self.discover = DiscoverPage(self)
        self.kobo_page = KoboPage(self)
        self.settings_page = SettingsPage(self)
        for p in (self.library, self.discover, self.kobo_page, self.settings_page):
            self.pages.addWidget(p)
        h.addWidget(self.pages, 1)
        self.nav.idClicked.connect(self.go)
        self.go(0)

        self.statusBar()
        find = QAction(self)
        find.setShortcut(QKeySequence.Find)
        find.triggered.connect(lambda: (self.go(0), self.library.search.setFocus()))
        self.addAction(find)

        self.kobo_timer = QTimer(self, interval=3000, timeout=self.check_kobo)
        self.kobo_timer.start()
        self.check_kobo(force=True)
        self.rescan()

        self.watcher = DownloadWatcher(self)
        self.watcher.new_epub.connect(self.import_from_downloads)
        self.apply_watch()

    # --- Navigation & Hilfen
    def go(self, i):
        self.pages.setCurrentIndex(i)
        self.nav.button(i).setChecked(True)
        if i == 2:
            self.kobo_page.refresh()

    def toast(self, msg):
        self.statusBar().showMessage(msg, 6000)

    def library_dir(self) -> str:
        d = self.settings.value("library_dir", str(default_library_dir()))
        Path(d).mkdir(parents=True, exist_ok=True)
        return d

    def downloads_dir(self) -> str:
        return self.settings.value("downloads_dir", default_downloads_dir())

    def apply_watch(self):
        if self.settings.value("watch_downloads", True, bool):
            if not self.watcher.start(self.downloads_dir()):
                self.toast(f"Ordner {self.downloads_dir()} gibt es nicht, Beobachten ist aus.")
        else:
            self.watcher.stop()

    def find_duplicate(self, title: str, authors: list[str]) -> Book | None:
        t = title.lower().strip()
        a = authors[0].lower().split()[-1] if authors else ""
        for b in self.books:
            if b.title.lower().strip() == t and (not a or a in b.author_str.lower()):
                return b
        return None

    def import_from_downloads(self, path: str):
        """Neues EPUB aus dem Download-Ordner übernehmen (läuft im Hintergrund)."""
        lib = self.library_dir()
        trash = self.settings.value("watch_trash", False, bool)

        def job():
            m = read_epub(path, with_cover=False)
            dup = self.find_duplicate(m.title or Path(path).stem, m.authors)
            if dup:
                return ("dup", m.title or Path(path).stem)
            import_epub(path, lib)
            return ("ok", m.title or Path(path).stem)

        def done(res):
            kind, title = res
            if kind == "dup":
                self.toast(f"„{title}“ ist schon in deiner Bibliothek, übersprungen.")
                return
            if trash:
                from PySide6.QtCore import QFile
                QFile.moveToTrash(path)
            self.toast(f"„{title}“ aus den Downloads übernommen.")
            self.rescan()

        run_bg(job, done=done, error=lambda m: self.toast(f"{Path(path).name} konnte nicht importiert werden: {m}"))

    def opds_feeds(self) -> list[dict]:
        try:
            return json.loads(self.settings.value("opds_feeds", "[]"))
        except (TypeError, ValueError):
            return []

    def on_kobo(self, b: Book) -> bool:
        return self.kobo_mount is not None and kobo.norm_name(Path(b.path).name) in self.kobo_names

    def in_library(self, rb: RemoteBook) -> Book | None:
        t = rb.title.lower().strip()
        for b in self.books:
            if b.title.lower().strip() == t:
                if not rb.authors or not b.authors or rb.authors[0].split()[-1].lower() in b.author_str.lower():
                    return b
        return None

    def show_in_library(self, b: Book):
        self.go(0)
        self.library.search.clear()
        for r in range(self.library.proxy.rowCount()):
            idx = self.library.proxy.index(r, 0)
            if idx.data(BookRole).path == b.path:
                self.library.grid.setCurrentIndex(idx)
                self.library.grid.scrollTo(idx)
                break

    # --- Bibliothek
    def rescan(self, after=None):
        if after:
            self._after_scan.append(after)
        if self._scanning:
            self._rescan_again = True
            return
        self._scanning = True
        self._rescan_again = False
        bar = self.library.scanbar

        def prog(a, b):
            if b > 5:
                bar.setVisible(True)
                bar.setMaximum(b)
                bar.setValue(a)

        def done(books):
            bar.setVisible(False)
            self._scanning = False
            self.books = books
            self.library.set_books(books)
            if self.pages.currentIndex() == 2:
                self.kobo_page.refresh()
            if self._rescan_again:
                self.rescan()
                return
            cbs, self._after_scan = self._after_scan, []
            for cb in cbs:
                cb()

        def err(msg):
            bar.setVisible(False)
            self._scanning = False
            QMessageBox.warning(self, "Bibliothek", f"Scan fehlgeschlagen: {msg}")

        run_bg(self.db.scan, self.library_dir(), done=done, error=err, progress=prog)

    def import_files(self, files: list[str]):
        epubs = [f for f in files if f.lower().endswith(".epub")]
        skipped = len(files) - len(epubs)
        if not epubs:
            QMessageBox.information(self, "Import", "Nur EPUB-Dateien können importiert werden.")
            return
        lib = self.library_dir()

        def job():
            return [import_epub(f, lib) for f in epubs]

        def done(_):
            msg = f"{len(epubs)} Buch importiert." if len(epubs) == 1 else f"{len(epubs)} Bücher importiert."
            if skipped:
                msg += f" {skipped} Datei(en) übersprungen, weil kein EPUB."
            self.toast(msg)
            self.rescan()

        run_bg(job, done=done, error=lambda m: QMessageBox.warning(self, "Import", m))

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        files = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if files:
            self.import_files(files)

    # --- Kobo
    def check_kobo(self, force=False):
        m = kobo.find_kobo()
        if m is None and not getattr(self, "_mounting", False):
            dev = kobo.find_unmounted_kobo()
            if dev is None:
                self._mount_failed.clear()  # abgesteckt: beim nächsten Anstecken neu versuchen
            elif dev not in self._mount_failed:
                self._auto_mount(dev)
        if m == self.kobo_mount and not force:
            return
        changed = m != self.kobo_mount
        self.kobo_mount = m
        if m:
            self.kobo_pill.setText(f"<span style='color:{style.GOOD}'>●</span>  Kobo verbunden")
            run_bg(kobo.books_on_kobo, m, done=self._kobo_names)
            if changed and not force:
                self.toast("Kobo erkannt.")
        else:
            self.kobo_names = set()
            self.kobo_pill.setText(f"<span style='color:{style.MUTED}'>○</span>  Kein Kobo verbunden")
            self._kobo_changed()

    def _auto_mount(self, dev: str):
        """Kobo ist angesteckt, aber niemand hat ihn eingehängt: selbst einhängen."""
        self._mounting = True
        self.kobo_pill.setText(f"<span style='color:{style.ACCENT}'>●</span>  Kobo wird eingehängt …")

        def done(_):
            self._mounting = False
            self.check_kobo(force=True)

        def fail(msg):
            self._mounting = False
            self._mount_failed.add(dev)
            self.toast(f"Kobo gefunden, aber Einhängen ging nicht: {msg}")

        run_bg(kobo.mount_kobo, dev, done=done, error=fail)

    def _kobo_names(self, names):
        self.kobo_names = names
        self._kobo_changed()

    def _kobo_changed(self):
        self.library.refresh_detail()
        if self.pages.currentIndex() == 2:
            self.kobo_page.refresh()

    def send_to_kobo(self, books: list[Book]):
        m = self.kobo_mount
        if not m or not books:
            return
        use_kepub = self.kobo_page.kepub.isChecked() and kobo.kepubify_available()
        bar = self.kobo_page.progress

        def job(progress=None):
            errors = []
            for i, b in enumerate(books):
                try:
                    kobo.transfer(b.path, m, b.title, b.author_str, use_kepub)
                except Exception as e:  # noqa: BLE001
                    errors.append(f"{b.title}: {e}")
                progress(i + 1, len(books))
            return errors

        def prog(a, n):
            bar.setVisible(True)
            bar.setMaximum(n)
            bar.setValue(a)
            self.toast(f"Übertrage auf den Kobo … {a}/{n}")

        def done(errors):
            bar.setVisible(False)
            ok = len(books) - len(errors)
            self.toast(f"{ok} Buch auf den Kobo übertragen." if ok == 1 else f"{ok} Bücher auf den Kobo übertragen.")
            if errors:
                QMessageBox.warning(self, "Kobo", "Einige Bücher gingen nicht:\n\n" + "\n".join(errors))
            self.check_kobo(force=True)

        run_bg(job, done=done, progress=prog, error=lambda e: QMessageBox.warning(self, "Kobo", e))


def main():
    os.environ.setdefault("QT_QPA_PLATFORMTHEME", "")
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setDesktopFileName("buecherregal")
    app.setStyle("Fusion")
    app.setStyleSheet(style.QSS)
    icon_path = Path(__file__).with_name("icon.svg")
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
