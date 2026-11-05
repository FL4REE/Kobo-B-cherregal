"""Gemeinsame Bausteine: Hintergrund-Worker, Cover-Rendering, Raster-Delegate, Modelle."""
from __future__ import annotations

import hashlib
import traceback

from PySide6.QtCore import (QAbstractListModel, QModelIndex, QObject, QRectF, QRunnable,
                            QSize, QSortFilterProxyModel, Qt, Signal)
from PySide6.QtGui import (QColor, QFont, QFontMetrics, QLinearGradient, QPainter,
                           QPainterPath, QPen, QPixmap)
from PySide6.QtWidgets import QStyle, QStyledItemDelegate

from . import style

BookRole = Qt.UserRole + 1
CARD_W, COVER_H, TEXT_H = 150, 225, 58


# ---------------------------------------------------------------- Worker

class WorkerSignals(QObject):
    done = Signal(object)
    error = Signal(str)
    progress = Signal(int, int)


class Worker(QRunnable):
    """Führt fn(*args, progress=cb) im Thread-Pool aus."""

    def __init__(self, fn, *args, with_progress=False, **kwargs):
        super().__init__()
        self.fn, self.args, self.kwargs = fn, args, kwargs
        self.with_progress = with_progress
        self.signals = WorkerSignals()

    def run(self):
        try:
            if self.with_progress:
                self.kwargs["progress"] = lambda a, b: self.signals.progress.emit(a, b)
            res = self.fn(*self.args, **self.kwargs)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self.signals.error.emit(str(e) or e.__class__.__name__)
        else:
            self.signals.done.emit(res)


# ---------------------------------------------------------------- Cover

def _palette_for(key: str) -> tuple[QColor, QColor]:
    h = int(hashlib.md5(key.encode()).hexdigest()[:6], 16)
    hue = h % 360
    return QColor.fromHsl(hue, 70, 70), QColor.fromHsl((hue + 40) % 360, 90, 35)


def placeholder_cover(title: str, author: str, w: int, h: int) -> QPixmap:
    """Generiertes Cover für Bücher ohne Titelbild."""
    pm = QPixmap(w, h)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    c1, c2 = _palette_for(title + author)
    g = QLinearGradient(0, 0, w, h)
    g.setColorAt(0, c1)
    g.setColorAt(1, c2)
    p.fillRect(0, 0, w, h, g)
    p.fillRect(0, 0, max(4, w // 22), h, QColor(0, 0, 0, 60))  # Buchrücken
    p.setPen(QColor(255, 255, 255, 235))
    f = QFont()
    f.setPointSizeF(max(9, w / 11))
    f.setBold(True)
    p.setFont(f)
    m = w * 0.12
    p.drawText(QRectF(m, h * 0.14, w - 2 * m, h * 0.56), Qt.AlignLeft | Qt.AlignTop | Qt.TextWordWrap, title)
    f.setBold(False)
    f.setPointSizeF(max(7, w / 17))
    p.setFont(f)
    p.setPen(QColor(255, 255, 255, 190))
    p.drawText(QRectF(m, h * 0.74, w - 2 * m, h * 0.2), Qt.AlignLeft | Qt.AlignBottom | Qt.TextWordWrap, author)
    p.end()
    return pm


def rounded(pm: QPixmap, w: int, h: int, radius: float = 8) -> QPixmap:
    """Pixmap auf w×h zuschneiden (Cover-Fill) und Ecken abrunden."""
    scaled = pm.scaled(w, h, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
    out = QPixmap(w, h)
    out.fill(Qt.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, w, h), radius, radius)
    p.setClipPath(path)
    p.drawPixmap((w - scaled.width()) // 2, (h - scaled.height()) // 2, scaled)
    p.end()
    return out


class CoverCache:
    def __init__(self):
        self._c: dict[tuple, QPixmap] = {}

    def get(self, key: str, title: str, author: str, w: int, h: int, pixmap: QPixmap | None = None,
            path: str = "") -> QPixmap:
        k = (key, w, h)
        if k in self._c:
            return self._c[k]
        src = pixmap
        if src is None and path:
            src = QPixmap(path)
        if src is None or src.isNull():
            src = placeholder_cover(title, author, w, h)
        pm = rounded(src, w, h)
        self._c[k] = pm
        return pm

    def drop(self, key: str):
        for k in [k for k in self._c if k[0] == key]:
            del self._c[k]


COVERS = CoverCache()


# ---------------------------------------------------------------- Delegate

class BookDelegate(QStyledItemDelegate):
    """Zeichnet ein Buch als Karte: Cover, Titel, Autor, Badges."""

    def __init__(self, parent=None, badge_fn=None):
        super().__init__(parent)
        self.badge_fn = badge_fn  # (book) -> list[(text, color)]

    def sizeHint(self, option, index):
        return QSize(CARD_W + 22, COVER_H + TEXT_H + 22)

    def paint(self, p: QPainter, option, index):
        book = index.data(BookRole)
        if book is None:
            return
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        r = option.rect
        x = r.x() + (r.width() - CARD_W) // 2
        y = r.y() + 10
        hovered = option.state & QStyle.State_MouseOver
        selected = option.state & QStyle.State_Selected

        if selected or hovered:
            bg = QColor(style.SURFACE_2) if selected else QColor(style.SURFACE)
            p.setPen(Qt.NoPen)
            p.setBrush(bg)
            p.drawRoundedRect(QRectF(r.x() + 3, r.y() + 3, r.width() - 6, r.height() - 6), 12, 12)

        # Schatten
        p.setPen(Qt.NoPen)
        for i, a in enumerate((28, 18, 10)):
            p.setBrush(QColor(0, 0, 0, a))
            p.drawRoundedRect(QRectF(x - i, y + 3 + i, CARD_W + 2 * i, COVER_H), 9, 9)

        pm = index.data(Qt.DecorationRole)
        p.drawPixmap(x, y, pm)
        if selected:
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor(style.ACCENT), 2))
            p.drawRoundedRect(QRectF(x - 1, y - 1, CARD_W + 2, COVER_H + 2), 9, 9)

        # Badges oben rechts
        if self.badge_fn:
            bx = x + CARD_W - 6
            f = QFont(option.font)
            f.setPointSizeF(7.5)
            f.setBold(True)
            p.setFont(f)
            fm = QFontMetrics(f)
            for text, color in self.badge_fn(book):
                tw = fm.horizontalAdvance(text) + 12
                rect = QRectF(bx - tw, y + 6, tw, 18)
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(20, 20, 24, 215))
                p.drawRoundedRect(rect, 9, 9)
                p.setPen(QColor(color))
                p.drawText(rect, Qt.AlignCenter, text)
                bx -= tw + 4

        # Text
        ty = y + COVER_H + 8
        f = QFont(option.font)
        f.setBold(True)
        f.setPointSizeF(9.6)
        p.setFont(f)
        p.setPen(QColor(style.TEXT))
        fm = QFontMetrics(f)
        title = book.title
        lines = _wrap2(title, fm, CARD_W)
        for i, line in enumerate(lines):
            p.drawText(x, ty + fm.ascent() + i * fm.height(), line)
        f2 = QFont(option.font)
        f2.setPointSizeF(8.8)
        p.setFont(f2)
        p.setPen(QColor(style.MUTED))
        fm2 = QFontMetrics(f2)
        ay = ty + len(lines) * fm.height() + 2 + fm2.ascent()
        p.drawText(x, ay, fm2.elidedText(book.author_str, Qt.ElideRight, CARD_W))
        p.restore()


def _wrap2(text: str, fm: QFontMetrics, width: int) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        test = f"{cur} {w}".strip()
        if fm.horizontalAdvance(test) <= width:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = w
            if len(lines) == 1:
                break
    if cur and len(lines) < 2:
        lines.append(cur)
    if len(lines) == 2:
        rest = text[len(lines[0]):].strip()
        lines[1] = fm.elidedText(rest, Qt.ElideRight, width)
    elif lines:
        lines[0] = fm.elidedText(lines[0], Qt.ElideRight, width)
    return lines or [""]


# ---------------------------------------------------------------- Modelle

class BookListModel(QAbstractListModel):
    """Funktioniert für lokale Books und RemoteBooks. Remote-Cover kommen über set_pixmap."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.items: list = []
        self.pixmaps: dict[str, QPixmap] = {}

    def set_items(self, items):
        self.beginResetModel()
        self.items = list(items)
        self.endResetModel()

    def append(self, items):
        if not items:
            return
        n = len(self.items)
        self.beginInsertRows(QModelIndex(), n, n + len(items) - 1)
        self.items.extend(items)
        self.endInsertRows()

    def key(self, b) -> str:
        return getattr(b, "path", None) or f"{b.source}:{b.id}"

    def set_pixmap(self, key: str, pm: QPixmap):
        self.pixmaps[key] = pm
        COVERS.drop(key)
        for row, b in enumerate(self.items):
            if self.key(b) == key:
                idx = self.index(row)
                self.dataChanged.emit(idx, idx, [Qt.DecorationRole])

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.items)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        b = self.items[index.row()]
        if role == BookRole:
            return b
        if role == Qt.DisplayRole:
            return b.title
        if role == Qt.ToolTipRole:
            return f"<b>{b.title}</b><br>{b.author_str}"
        if role == Qt.DecorationRole:
            k = self.key(b)
            return COVERS.get(k, b.title, b.author_str, CARD_W, COVER_H,
                              pixmap=self.pixmaps.get(k), path=getattr(b, "cover_path", ""))
        return None


class LibraryFilter(QSortFilterProxyModel):
    SORTS = ["Zuletzt hinzugefügt", "Titel", "Autor", "Serie"]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.text = ""
        self.german_only = False
        self.sort_mode = 0
        self.setDynamicSortFilter(True)

    def set_text(self, t):
        self.text = t.lower().strip()
        self.invalidateFilter()

    def set_german_only(self, v):
        self.german_only = v
        self.invalidateFilter()

    def set_sort(self, mode):
        self.sort_mode = mode
        self.invalidate()
        self.sort(0, Qt.DescendingOrder if mode == 0 else Qt.AscendingOrder)

    def filterAcceptsRow(self, row, parent):
        b = self.sourceModel().items[row]
        if self.german_only and not b.is_german:
            return False
        if self.text:
            hay = " ".join([b.title, b.author_str, b.series, " ".join(b.subjects)]).lower()
            return all(part in hay for part in self.text.split())
        return True

    def _key(self, b):
        m = self.sort_mode
        if m == 0:
            return b.added
        if m == 1:
            return b.title.lower()
        if m == 2:
            last = b.authors[0].split()[-1].lower() if b.authors else "~"
            return (last, b.title.lower())
        try:
            idx = float(b.series_index or 0)
        except ValueError:
            idx = 0
        return (b.series.lower() or "~", idx, b.title.lower())

    def lessThan(self, a, b):
        items = self.sourceModel().items
        return self._key(items[a.row()]) < self._key(items[b.row()])
