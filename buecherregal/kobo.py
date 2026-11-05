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


def find_unmounted_kobo() -> str | None:
    """Sucht einen angeschlossenen, aber nicht eingehängten Kobo (z.B. unter Hyprland ohne
    Automounter). Gibt das Blockgerät zurück, etwa /dev/sdb."""
    import json
    try:
        out = subprocess.run(
            ["lsblk", "-J", "-o", "PATH,LABEL,FSTYPE,MOUNTPOINTS,VENDOR,MODEL"],
            capture_output=True, text=True, timeout=5, check=True).stdout
        devs = json.loads(out).get("blockdevices", [])
    except (OSError, subprocess.SubprocessError, ValueError):
        return None

    def walk(nodes, parent_is_kobo=False):
        for d in nodes:
            ident = " ".join(str(d.get(k) or "") for k in ("label", "vendor", "model")).lower()
            is_kobo = parent_is_kobo or "kobo" in ident
            mounted = any(m for m in (d.get("mountpoints") or []) if m)
            if is_kobo and d.get("fstype") == "vfat" and not mounted:
                return d["path"]
            hit = walk(d.get("children") or [], is_kobo)
            if hit:
                return hit
        return None

    return walk(devs)


def mount_kobo(dev: str) -> Path | None:
    """Kobo per udisksctl einhängen (ohne sudo, wie es ein Dateimanager auch macht)."""
    try:
        r = subprocess.run(["udisksctl", "mount", "-b", dev, "--no-user-interaction"],
                           capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError) as e:
        raise RuntimeError(f"udisksctl nicht verfügbar: {e}") from e
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip() or "Einhängen fehlgeschlagen")
    # Ausgabe: "Mounted /dev/sdb at /run/media/erik/KOBOeReader"
    out = r.stdout.strip()
    if " at " in out:
        return Path(out.split(" at ", 1)[1].rstrip("."))
    return find_kobo()


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


def norm_name(filename: str) -> str:
    """Dateiname für den Vergleich vereinheitlichen: ohne .epub/.kepub, klein, Unicode-normalisiert.
    So passen 'Buch.epub', 'Buch.kepub.epub' und Umlaute in verschiedener Schreibweise zusammen."""
    import unicodedata
    n = unicodedata.normalize("NFC", filename).lower().strip()
    for suffix in (".epub", ".kepub"):
        if n.endswith(suffix):
            n = n[: -len(suffix)]
    return n


def books_on_kobo(mount: Path) -> set[str]:
    """Normalisierte Namen (siehe norm_name) aller Bücher auf dem Kobo."""
    names = set()
    for p in mount.rglob("*"):
        if p.is_file() and not any(part.startswith(".") for part in p.relative_to(mount).parts):
            if p.name.lower().endswith(".epub"):
                names.add(norm_name(p.name))
    return names


def kepubify_available() -> bool:
    return shutil.which("kepubify") is not None


def transfer(book_path: str, mount: Path, title: str, author: str, use_kepub: bool = False) -> str:
    """Kopiert ein Buch auf den Kobo. Mit kepubify wird es vorher nach KEPUB konvertiert
    (schnellere Seitenwechsel, Lesestatistik und bessere Silbentrennung auf dem Kobo)."""
    target_dir = mount / KOBO_SUBDIR
    target_dir.mkdir(exist_ok=True)
    base = Path(book_path).stem
    if base.lower().endswith(".kepub"):  # schon KEPUB: unverändert kopieren
        dest = target_dir / Path(book_path).name
        shutil.copyfile(book_path, dest)
        os.sync()
        return str(dest)
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
