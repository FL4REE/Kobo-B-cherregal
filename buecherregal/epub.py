"""EPUB-Metadaten und Cover auslesen, ohne externe Abhängigkeiten."""
from __future__ import annotations

import html
import posixpath
import re
import zipfile
from dataclasses import dataclass, field
from xml.etree import ElementTree as ET

NS = {
    "c": "urn:oasis:names:tc:opendocument:xmlns:container",
    "opf": "http://www.idpf.org/2007/opf",
    "dc": "http://purl.org/dc/elements/1.1/",
}


@dataclass
class EpubMeta:
    title: str = ""
    authors: list[str] = field(default_factory=list)
    language: str = ""
    description: str = ""
    publisher: str = ""
    date: str = ""
    series: str = ""
    series_index: str = ""
    subjects: list[str] = field(default_factory=list)
    isbn: str = ""
    cover: bytes | None = None


def _text(el) -> str:
    return (el.text or "").strip() if el is not None else ""


def clean_description(raw: str) -> str:
    """HTML aus Beschreibungen entfernen, Absätze behalten."""
    if not raw:
        return ""
    s = re.sub(r"(?i)<br\s*/?>|</p>|</div>", "\n", raw)
    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s)
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n\s*\n+", "\n\n", s)
    return s.strip()


def _find_opf(z: zipfile.ZipFile) -> str:
    try:
        root = ET.fromstring(z.read("META-INF/container.xml"))
        rf = root.find(".//c:rootfile", NS)
        if rf is not None and rf.get("full-path"):
            return rf.get("full-path")
    except (KeyError, ET.ParseError):
        pass
    for n in z.namelist():
        if n.lower().endswith(".opf"):
            return n
    raise ValueError("Keine OPF-Datei gefunden")


def _resolve(base: str, href: str) -> str:
    from urllib.parse import unquote

    href = unquote(href.split("#")[0])
    return posixpath.normpath(posixpath.join(posixpath.dirname(base), href))


def _read_cover(z: zipfile.ZipFile, opf_path: str, pkg) -> bytes | None:
    manifest = pkg.find("opf:manifest", NS)
    if manifest is None:
        return None
    items = manifest.findall("opf:item", NS)
    by_id = {i.get("id"): i for i in items}
    candidate = None

    # EPUB 3: properties="cover-image"
    for i in items:
        if "cover-image" in (i.get("properties") or "").split():
            candidate = i
            break
    # EPUB 2: <meta name="cover" content="id">
    if candidate is None:
        for m in pkg.findall("opf:metadata/opf:meta", NS):
            if m.get("name") == "cover" and m.get("content") in by_id:
                candidate = by_id[m.get("content")]
                break
    # Heuristik: Bild mit "cover" im Namen/ID
    if candidate is None:
        for i in items:
            mt = i.get("media-type", "")
            if mt.startswith("image/") and "cover" in (
                (i.get("id") or "") + (i.get("href") or "")
            ).lower():
                candidate = i
                break
    if candidate is not None:
        # Manche Bücher zeigen auf eine XHTML-Seite statt aufs Bild
        if not candidate.get("media-type", "").startswith("image/"):
            try:
                page_path = _resolve(opf_path, candidate.get("href"))
                page = z.read(page_path).decode("utf-8", "ignore")
                m = re.search(r'(?:src|xlink:href|href)="([^"]+\.(?:jpe?g|png|gif|webp))"', page, re.I)
                if m:
                    return z.read(_resolve(page_path, m.group(1)))
            except KeyError:
                pass
            return None
        try:
            return z.read(_resolve(opf_path, candidate.get("href")))
        except KeyError:
            return None
    # Letzter Versuch: erstes Bild überhaupt
    for i in items:
        if i.get("media-type", "").startswith("image/"):
            try:
                return z.read(_resolve(opf_path, i.get("href")))
            except KeyError:
                continue
    return None


def read_epub(path: str, with_cover: bool = True) -> EpubMeta:
    meta = EpubMeta()
    with zipfile.ZipFile(path) as z:
        opf_path = _find_opf(z)
        pkg = ET.fromstring(z.read(opf_path))
        md = pkg.find("opf:metadata", NS)
        if md is None:
            md = pkg
        meta.title = _text(md.find("dc:title", NS))
        meta.authors = [_text(a) for a in md.findall("dc:creator", NS) if _text(a)]
        meta.language = _text(md.find("dc:language", NS)).lower()
        meta.description = clean_description(_text(md.find("dc:description", NS)))
        meta.publisher = _text(md.find("dc:publisher", NS))
        meta.date = _text(md.find("dc:date", NS))[:10]
        meta.subjects = [_text(s) for s in md.findall("dc:subject", NS) if _text(s)]
        for ident in md.findall("dc:identifier", NS):
            v = _text(ident)
            digits = re.sub(r"[^0-9Xx]", "", v)
            if "isbn" in v.lower() or "isbn" in str(ident.attrib).lower() or len(digits) in (10, 13) and digits.startswith(("97", "0", "3")):
                meta.isbn = digits
                break

        # Serie: Calibre-Stil (EPUB 2) oder belongs-to-collection (EPUB 3)
        metas = md.findall("opf:meta", NS)
        for m in metas:
            name = m.get("name")
            if name == "calibre:series":
                meta.series = m.get("content", "")
            elif name == "calibre:series_index":
                meta.series_index = m.get("content", "")
        if not meta.series:
            for m in metas:
                if m.get("property") == "belongs-to-collection":
                    meta.series = _text(m)
                    mid = m.get("id")
                    for r in metas:
                        if r.get("refines") == f"#{mid}" and r.get("property") == "group-position":
                            meta.series_index = _text(r)
                    break
        if meta.series_index.endswith(".0"):
            meta.series_index = meta.series_index[:-2]

        if with_cover:
            meta.cover = _read_cover(z, opf_path, pkg)
    return meta
