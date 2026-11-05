#!/usr/bin/env bash
# Richtet Bücherregal als normale App ein: Python-Umgebung, Icon und Startmenü-Eintrag.
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

# 1. Python-Umgebung vorbereiten, damit der erste Start aus dem Menü sofort klappt
if ! python3 -c "import PySide6" 2>/dev/null && [ ! -x .venv/bin/python ]; then
    echo "Richte Python-Umgebung ein (einmalig) …"
    python3 -m venv .venv
    .venv/bin/pip install -q --upgrade pip
    .venv/bin/pip install -q -r requirements.txt
fi
chmod +x start.sh

# 2. Icon
ICONS="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor"
mkdir -p "$ICONS/scalable/apps"
cp buecherregal/icon.svg "$ICONS/scalable/apps/buecherregal.svg"
if command -v rsvg-convert >/dev/null; then
    for s in 48 64 128 256 512; do
        mkdir -p "$ICONS/${s}x${s}/apps"
        rsvg-convert -w $s -h $s buecherregal/icon.svg -o "$ICONS/${s}x${s}/apps/buecherregal.png"
    done
fi
gtk-update-icon-cache -q -t "$ICONS" 2>/dev/null || true

# 3. Startmenü-Eintrag
APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
mkdir -p "$APPS"
cat > "$APPS/buecherregal.desktop" <<DESK
[Desktop Entry]
Type=Application
Name=Bücherregal
GenericName=E-Book-Bibliothek
Comment=Deutsche EPUBs verwalten und auf den Kobo übertragen
Keywords=ebook;epub;kobo;bücher;buch;bibliothek;lesen;library;
Exec="$DIR/start.sh"
Icon=buecherregal
StartupWMClass=buecherregal
StartupNotify=true
Categories=Office;Viewer;
Terminal=false
DESK
chmod +x "$APPS/buecherregal.desktop"
update-desktop-database -q "$APPS" 2>/dev/null || true

echo "Fertig. Such im App-Starter nach „Bücherregal“ (oder „ebook“, „kobo“)."
