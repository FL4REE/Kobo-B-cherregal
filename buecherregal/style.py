"""Farben und Stylesheet. Dunkles, warmes Theme mit Bernstein-Akzent."""

BG = "#15161a"
SURFACE = "#1d1f24"
SURFACE_2 = "#262931"
BORDER = "#30333c"
TEXT = "#ece9e3"
MUTED = "#9a9aa3"
ACCENT = "#e0a458"
ACCENT_HOVER = "#ecb873"
GOOD = "#7cc49a"
BAD = "#e07a6a"

QSS = f"""
* {{
    font-family: "Inter", "Noto Sans", "Cantarell", sans-serif;
    font-size: 10.5pt;
    color: {TEXT};
}}
QMainWindow, QWidget#root {{ background: {BG}; }}
QWidget#page {{ background: {BG}; }}

/* Seitenleiste */
QFrame#sidebar {{ background: {SURFACE}; border-right: 1px solid {BORDER}; }}
QLabel#logo {{ font-size: 15pt; font-weight: 700; color: {TEXT}; padding: 4px 6px 18px 6px; }}
QLabel#logoSub {{ color: {MUTED}; font-size: 9pt; padding: 0 6px; }}
QPushButton#nav {{
    text-align: left; padding: 10px 14px; border: none; border-radius: 8px;
    background: transparent; color: {MUTED}; font-weight: 600;
}}
QPushButton#nav:hover {{ background: {SURFACE_2}; color: {TEXT}; }}
QPushButton#nav:checked {{ background: {SURFACE_2}; color: {ACCENT}; }}
QLabel#koboPill {{
    border-radius: 8px; padding: 8px 10px; background: {SURFACE_2}; color: {MUTED}; font-size: 9pt;
}}

/* Überschriften */
QLabel#h1 {{ font-size: 20pt; font-weight: 700; }}
QLabel#h2 {{ font-size: 15pt; font-weight: 700; }}
QLabel#muted {{ color: {MUTED}; }}
QLabel#chip {{
    background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 10px;
    padding: 3px 9px; color: {TEXT}; font-size: 9pt;
}}
QLabel#chipAccent {{
    background: rgba(224,164,88,0.15); border: 1px solid rgba(224,164,88,0.45);
    border-radius: 10px; padding: 3px 9px; color: {ACCENT}; font-size: 9pt;
}}

/* Eingaben */
QLineEdit, QComboBox, QSpinBox {{
    background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 9px;
    padding: 8px 12px; selection-background-color: {ACCENT}; selection-color: #1a1a1a;
}}
QLineEdit:focus, QComboBox:focus {{ border-color: {ACCENT}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{
    background: {SURFACE}; border: 1px solid {BORDER}; selection-background-color: {SURFACE_2};
    selection-color: {ACCENT}; outline: none; padding: 4px;
}}
QCheckBox {{ spacing: 8px; color: {TEXT}; }}
QCheckBox::indicator {{
    width: 16px; height: 16px; border-radius: 5px; border: 1px solid {BORDER}; background: {SURFACE};
}}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}

/* Buttons */
QPushButton {{
    background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 9px;
    padding: 8px 16px; font-weight: 600;
}}
QPushButton:hover {{ border-color: {ACCENT}; }}
QPushButton:disabled {{ color: #5c5d64; border-color: {SURFACE_2}; }}
QPushButton#primary {{ background: {ACCENT}; color: #1b1712; border: none; }}
QPushButton#primary:hover {{ background: {ACCENT_HOVER}; }}
QPushButton#primary:disabled {{ background: #4a4235; color: #8b8170; }}
QPushButton#danger {{ background: transparent; color: {BAD}; border: 1px solid rgba(224,122,106,0.4); }}
QPushButton#danger:hover {{ background: rgba(224,122,106,0.12); }}
QPushButton#ghost {{ background: transparent; border: none; color: {MUTED}; }}
QPushButton#ghost:hover {{ color: {TEXT}; }}

/* Listen und Raster */
QListView, QListWidget, QTreeWidget {{ background: transparent; border: none; outline: none; }}
QListWidget#plain {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 10px; padding: 4px; }}
QListWidget#plain::item {{ padding: 8px; border-radius: 6px; }}
QListWidget#plain::item:selected {{ background: {SURFACE_2}; color: {ACCENT}; }}
QTreeWidget {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 10px; padding: 4px; }}
QTreeWidget::item {{ padding: 6px 2px; }}
QTreeWidget::item:selected {{ background: {SURFACE_2}; color: {TEXT}; }}
QHeaderView::section {{
    background: {SURFACE}; color: {MUTED}; border: none; border-bottom: 1px solid {BORDER};
    padding: 6px; font-weight: 600;
}}

/* Detailspalte */
QFrame#detail {{ background: {SURFACE}; border-left: 1px solid {BORDER}; }}
QFrame#card {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 14px; }}

QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {BORDER}; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: #464a55; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}

QProgressBar {{
    background: {SURFACE_2}; border: none; border-radius: 4px; height: 8px; text-align: center;
    color: transparent;
}}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 4px; }}

QStatusBar {{ background: {SURFACE}; color: {MUTED}; border-top: 1px solid {BORDER}; }}
QToolTip {{ background: {SURFACE_2}; color: {TEXT}; border: 1px solid {BORDER}; padding: 6px; }}
QMessageBox, QDialog {{ background: {SURFACE}; }}
QMenu {{ background: {SURFACE}; border: 1px solid {BORDER}; padding: 4px; }}
QMenu::item {{ padding: 6px 18px; border-radius: 6px; }}
QMenu::item:selected {{ background: {SURFACE_2}; color: {ACCENT}; }}
"""
