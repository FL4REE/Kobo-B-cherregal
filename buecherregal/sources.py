"""Legale Quellen für gemeinfreie deutsche E-Books.

* Project Gutenberg (über die Gutendex-API), gefiltert auf Deutsch + EPUB
* Wikisource (de), EPUB-Export über ws-export
* Internet Archive, nur als gemeinfrei markierte deutsche Scans
* Beliebige OPDS-Kataloge (z.B. eigener Calibre-Server, Kiosk der Stadtbibliothek)
"""
from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from xml.etree import ElementTree as ET

UA = "Buecherregal/1.0 (private E-Book-Verwaltung)"
TIMEOUT = 30


@dataclass
class RemoteBook:
    source: str
    id: str
    title: str
    authors: list[str] = field(default_factory=list)
    epub_url: str = ""
    cover_url: str = ""
    description: str = ""
    language: str = "de"
    extra: str = ""

    @property
    def author_str(self) -> str:
        return ", ".join(self.authors) if self.authors else "Unbekannt"


def http_get(url: str, timeout: int = TIMEOUT) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def download(url: str, dest: str, progress=None) -> None:
    if url.startswith("ia:"):
        url = InternetArchiveSource.resolve_epub(url[3:])
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=120) as r, open(dest, "wb") as fh:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        while chunk := r.read(64 * 1024):
            fh.write(chunk)
            done += len(chunk)
            if progress:
                progress(done, total)
    with open(dest, "rb") as fh:
        if fh.read(2) != b"PK":
            raise ValueError("Die heruntergeladene Datei ist kein gültiges EPUB.")


def _flip_name(n: str) -> str:
    """'Kafka, Franz' -> 'Franz Kafka'"""
    if "," in n:
        last, first = [x.strip() for x in n.split(",", 1)]
        first = re.sub(r"\s*\(.*\)", "", first)
        return f"{first} {last}".strip()
    return n


class GutenbergSource:
    name = "Project Gutenberg"
    API = "https://gutendex.com/books/"

    def search(self, query: str, page: int = 1) -> tuple[list[RemoteBook], bool]:
        params = {"languages": "de", "mime_type": "application/epub", "page": page}
        if query.strip():
            params["search"] = query.strip()
        else:
            params["sort"] = "popular"
        data = json.loads(http_get(self.API + "?" + urllib.parse.urlencode(params)))
        out = []
        for b in data.get("results", []):
            fmts = b.get("formats", {})
            epub = next((u for k, u in fmts.items() if k.startswith("application/epub")), "")
            if not epub or "de" not in b.get("languages", []):
                continue
            authors = [_flip_name(a["name"]) for a in b.get("authors", [])]
            years = ""
            if b.get("authors"):
                a = b["authors"][0]
                if a.get("birth_year") or a.get("death_year"):
                    years = f"{a.get('birth_year') or '?'}–{a.get('death_year') or '?'}"
            subjects = [s.split(" -- ")[0] for s in b.get("subjects", [])][:4]
            summary = " ".join(b.get("summaries", []) or [])
            out.append(RemoteBook(
                source=self.name,
                id=str(b["id"]),
                title=b.get("title", "").replace("\r\n", " ").replace("\n", " "),
                authors=authors,
                epub_url=epub,
                cover_url=fmts.get("image/jpeg", ""),
                description=summary or ("Themen: " + ", ".join(subjects) if subjects else ""),
                extra=f"{years}  ·  {b.get('download_count', 0)} Downloads".strip(" ·"),
            ))
        return out, bool(data.get("next"))


class WikisourceSource:
    name = "Wikisource"
    API = "https://de.wikisource.org/w/api.php"
    EXPORT = "https://ws-export.wmcloud.org/?lang=de&format=epub-3&page="

    def search(self, query: str, page: int = 1) -> tuple[list[RemoteBook], bool]:
        if not query.strip():
            return [], False
        per = 20
        params = {
            "action": "query", "format": "json", "list": "search", "srnamespace": 0,
            "srsearch": query, "srlimit": per, "sroffset": (page - 1) * per, "srprop": "snippet",
        }
        data = json.loads(http_get(self.API + "?" + urllib.parse.urlencode(params)))
        out = []
        for r in data.get("query", {}).get("search", []):
            title = r["title"]
            if "/" in title:  # Unterseiten (einzelne Kapitel) überspringen
                continue
            snippet = re.sub(r"<[^>]+>", "", r.get("snippet", ""))
            out.append(RemoteBook(
                source=self.name, id=title, title=title,
                epub_url=self.EXPORT + urllib.parse.quote(title),
                description=snippet,
                extra="Gemeinfreier Text aus Wikisource",
            ))
        return out, "continue" in data


ATOM = "{http://www.w3.org/2005/Atom}"
DC = "{http://purl.org/dc/terms/}"
DC11 = "{http://purl.org/dc/elements/1.1/}"


class OPDSSource:
    """Generischer OPDS-Katalog. Es werden nur EPUB-Links und deutsche Titel übernommen
    (Einträge ohne Sprachangabe werden ebenfalls angezeigt)."""

    def __init__(self, name: str, url: str):
        self.name = name
        self.url = url
        self._search_tpl: str | None = None
        self._next: str = ""

    def _search_template(self) -> str | None:
        if self._search_tpl is not None:
            return self._search_tpl
        root = ET.fromstring(http_get(self.url))
        for link in root.findall(f"{ATOM}link"):
            if link.get("rel") == "search":
                href = urllib.parse.urljoin(self.url, link.get("href"))
                if "{searchTerms}" in href:
                    self._search_tpl = href
                elif "opensearchdescription" in (link.get("type") or ""):
                    desc = ET.fromstring(http_get(href))
                    for u in desc.iter():
                        if u.tag.endswith("Url") and "atom" in (u.get("type") or ""):
                            self._search_tpl = urllib.parse.urljoin(href, u.get("template"))
                break
        return self._search_tpl or ""

    def search(self, query: str, page: int = 1) -> tuple[list[RemoteBook], bool]:
        if page > 1 and self._next:
            url = self._next
        elif query.strip():
            tpl = self._search_template()
            if not tpl:
                raise RuntimeError("Dieser Katalog bietet keine Suche an.")
            url = tpl.replace("{searchTerms}", urllib.parse.quote(query))
            url = re.sub(r"\{[^}]+\?\}", "", url)
        else:
            url = self.url
        root = ET.fromstring(http_get(url))
        out = []
        for e in root.findall(f"{ATOM}entry"):
            lang = (e.findtext(f"{DC}language") or e.findtext(f"{DC11}language") or "").lower()
            if lang and not lang.startswith(("de", "ger", "deu")):
                continue
            epub = cover = ""
            for l in e.findall(f"{ATOM}link"):
                t, rel = l.get("type") or "", l.get("rel") or ""
                href = urllib.parse.urljoin(url, l.get("href") or "")
                if "epub" in t and "acquisition" in rel and not epub:
                    epub = href
                elif "image" in rel and "thumbnail" not in rel:
                    cover = href
                elif "thumbnail" in rel and not cover:
                    cover = href
            if not epub:
                continue
            authors = [a.findtext(f"{ATOM}name") or "" for a in e.findall(f"{ATOM}author")]
            summary = e.findtext(f"{ATOM}summary") or e.findtext(f"{ATOM}content") or ""
            out.append(RemoteBook(
                source=self.name, id=e.findtext(f"{ATOM}id") or epub,
                title=e.findtext(f"{ATOM}title") or "Ohne Titel",
                authors=[a for a in authors if a], epub_url=epub, cover_url=cover,
                description=re.sub(r"<[^>]+>", "", summary).strip(), language=lang or "?",
            ))
        self._next = ""
        for l in root.findall(f"{ATOM}link"):
            if l.get("rel") == "next":
                self._next = urllib.parse.urljoin(url, l.get("href") or "")
        return out, bool(self._next)


class InternetArchiveSource:
    """Internet Archive: nur deutsche Texte mit EPUB, die als gemeinfrei markiert und
    nicht nur ausleihbar sind. Das sind meist alte Scans (oft Fraktur), die EPUBs
    entstehen per Texterkennung, die Qualität schwankt also."""

    name = "Internet Archive"
    SEARCH = "https://archive.org/advancedsearch.php"
    BASE_Q = ("language:(ger OR German) AND mediatype:texts AND format:EPUB "
              "AND licenseurl:*publicdomain* AND NOT access-restricted-item:true")

    def search(self, query: str, page: int = 1) -> tuple[list[RemoteBook], bool]:
        q = self.BASE_Q + (f" AND ({query.strip()})" if query.strip() else "")
        rows = 30
        params = [("q", q), ("rows", rows), ("page", page), ("output", "json"),
                  ("sort[]", "downloads desc")]
        for f in ("identifier", "title", "creator", "date", "description", "downloads"):
            params.append(("fl[]", f))
        data = json.loads(http_get(self.SEARCH + "?" + urllib.parse.urlencode(params)))
        resp = data.get("response", {})
        out = []
        for d in resp.get("docs", []):
            ident = d["identifier"]
            creators = d.get("creator") or []
            if isinstance(creators, str):
                creators = [creators]
            authors = []
            for c in creators:
                c = re.sub(r"\[.*?\]", "", c)
                c = re.sub(r",?\s*\d{4}\s*-\s*(\d{4})?\.?", "", c).strip(" ,")
                if c:
                    authors.append(_flip_name(c))
            desc = d.get("description") or ""
            if isinstance(desc, list):
                desc = " ".join(desc)
            year = str(d.get("date", ""))[:4]
            out.append(RemoteBook(
                source=self.name, id=ident,
                title=re.sub(r"\s*\[.*?\]", "", str(d.get("title", ident))).strip(" ;/:,"),
                authors=authors,
                epub_url=f"ia:{ident}",  # echte Datei wird beim Download nachgeschlagen
                cover_url=f"https://archive.org/services/img/{ident}",
                description=re.sub(r"<[^>]+>", "", desc).strip()[:1500],
                extra=" · ".join(x for x in (year, "Gemeinfrei, Scan mit Texterkennung") if x),
            ))
        found = resp.get("numFound", 0)
        return out, page * rows < found

    @staticmethod
    def resolve_epub(ident: str) -> str:
        meta = json.loads(http_get(f"https://archive.org/metadata/{urllib.parse.quote(ident)}"))
        for f in meta.get("files", []):
            if f.get("format") == "EPUB" and not f.get("private"):
                return f"https://archive.org/download/{urllib.parse.quote(ident)}/{urllib.parse.quote(f['name'])}"
        raise ValueError("Für dieses Buch gibt es keine frei herunterladbare EPUB-Datei.")
