"""Kobo-eReader erkennen und Bücher übertragen."""
from __future__ import annotations

import getpass
import os
import shutil
import subprocess
import tempfile
from pathlib import Path


KOBO_SUBDIR = "Bücher"  # Unterordner auf dem Kobo, der Kobo findet Bücher überall


def find_kobo() -> Path | None:
    """Sucht einen eingehängten Kobo (erkennbar am versteckten .kobo-Ordner)."""
    user = getpass.getuser()
    roots = [
        Path(f"/run/media/{user}"),  # udisks2 auf Arch/CachyOS
        Path(f"/media/{user}"),      # Debian/Ubuntu
        Path("/media"),
        Path("/mnt"),
    ]
    for root in roots:
        try:
            for d in root.iterdir():
                if (d / ".kobo").is_dir():
                    return d
        except (OSError, PermissionError):
            continue
    # Fallback: /proc/mounts nach vfat-Mounts mit .kobo durchsuchen
    try:
        with open("/proc/mounts") as fh:
            for line in fh:
                parts = line.split()
                if len(parts) > 1:
                    mp = Path(parts[1].replace("\\040", " "))
                    if (mp / ".kobo").is_dir():
                        return mp
    except OSError:
        pass
    return None


def kobo_info(mount: Path) -> dict:
    info = {"mount": str(mount), "model": "Kobo", "free": 0, "total": 0}
    try:
        ver = (mount / ".kobo" / "version").read_text().strip().split(",")
        if len(ver) >= 3:
            info["firmware"] = ver[2]
    except OSError:
        pass
    try:
        du = shutil.disk_usage(mount)
        info["free"], info["total"] = du.free, du.total
    except OSError:
        pass
    return info


def books_on_kobo(mount: Path) -> set[str]:
    """Dateinamen (ohne Endung, kleingeschrieben) aller Bücher auf dem Kobo."""
    names = set()
    for p in mount.rglob("*"):
        if p.is_file() and not any(part.startswith(".") for part in p.relative_to(mount).parts):
            n = p.name.lower()
            if n.endswith(".kepub.epub"):
                names.add(n[: -len(".kepub.epub")])
            elif n.endswith(".epub"):
                names.add(n[: -len(".epub")])
    return names


def kepubify_available() -> bool:
    return shutil.which("kepubify") is not None


def transfer(book_path: str, mount: Path, title: str, author: str, use_kepub: bool = False) -> str:
    """Kopiert ein Buch auf den Kobo. Mit kepubify wird es vorher nach KEPUB konvertiert
    (schnellere Seitenwechsel, Lesestatistik und bessere Silbentrennung auf dem Kobo)."""
    target_dir = mount / KOBO_SUBDIR
    target_dir.mkdir(exist_ok=True)
    base = Path(book_path).stem
    if use_kepub and kepubify_available():
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["kepubify", "-o", tmp, book_path], check=True,
                           capture_output=True, timeout=300)
            out = next(Path(tmp).glob("*.kepub.epub"))
            dest = target_dir / f"{base}.kepub.epub"
            shutil.copyfile(out, dest)
    else:
        dest = target_dir / f"{base}.epub"
        shutil.copyfile(book_path, dest)
    os.sync()
    return str(dest)


def eject(mount: Path) -> tuple[bool, str]:
    """Kobo sicher auswerfen über udisksctl."""
    try:
        src = None
        with open("/proc/mounts") as fh:
            for line in fh:
                parts = line.split()
                if len(parts) > 1 and Path(parts[1].replace("\\040", " ")) == mount:
                    src = parts[0]
        if not src:
            return False, "Gerät nicht gefunden"
        os.sync()
        subprocess.run(["udisksctl", "unmount", "-b", src], check=True, capture_output=True, timeout=30)
        subprocess.run(["udisksctl", "power-off", "-b", src.rstrip("0123456789")],
                       capture_output=True, timeout=30)
        return True, "Kobo kann jetzt abgesteckt werden."
    except FileNotFoundError:
        return False, "udisksctl ist nicht installiert (Paket udisks2)."
    except subprocess.CalledProcessError as e:
        return False, (e.stderr or b"").decode(errors="ignore").strip() or "Auswerfen fehlgeschlagen"
