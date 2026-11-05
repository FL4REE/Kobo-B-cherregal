#!/usr/bin/env bash
# Startet Bücherregal. Beim ersten Start wird eine eigene Python-Umgebung mit PySide6 angelegt.
set -e
cd "$(dirname "$(readlink -f "$0")")"
if python3 -c "import PySide6" 2>/dev/null; then
    exec python3 -m buecherregal "$@"
fi
if [ ! -x .venv/bin/python ]; then
    echo "Erster Start: richte Python-Umgebung ein (einmalig, ca. 1 Minute) …"
    python3 -m venv .venv
    .venv/bin/pip install -q --upgrade pip
    .venv/bin/pip install -q -r requirements.txt
fi
exec .venv/bin/python -m buecherregal "$@"
