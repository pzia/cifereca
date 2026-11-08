"""Offline public bibliographic evidence extraction for BiblioAccess 1409.

This parses only structured, bounded metadata from a known book page. It does
not infer an entitlement, loan, format license, offer, or authenticated access.
No HTTP, JavaScript execution, token preservation or cross-tenant lookup.
"""
from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
import json
import re
from urllib.parse import urlsplit

from biblioaccess_observations import MAX_HTML_BYTES, book_id_from_url

MAX_SCRIPT_BYTES = 100_000
_MAX_TEXT = 1_024
_ISBN13 = re.compile(r"97[89][0-9]{10}\Z")


@dataclass(frozen=True)
class BibliographicNotice:
    book_id: str
    title: str
    author: str | None = None
    publisher: str | None = None
    isbn13: str | None = None
    published_at: str | None = None
    image_url: str | None = None
    source_kind: str = "structured_metadata"
    entitlement: str = "unverified"
    acquisition: str = "unsupported"


def _short(value):
    if type(value) is not str:
        return None
    text = re.sub(r"\s+", " ", value).strip()
    if not text or len(text) > _MAX_TEXT:
        return None
    return text


def _name(value):
    if isinstance(value, dict):
        return _short(value.get("name"))
    if type(value) is list:
        parts = [_name(entry) for entry in value[:12]]
        joined = ", ".join(part for part in parts if part)
        return _short(joined)
    return _short(value)


def _isbn(value):
    text = re.sub(r"[\s-]", "", value) if type(value) is str else ""
    if not _ISBN13.fullmatch(text):
        return None
    digits = list(map(int, text))
    check = (10 - sum(d * (1 if i % 2 == 0 else 3)
                      for i, d in enumerate(digits[:12])) % 10) % 10
    return text if check == digits[-1] else None


def _image(value):
    if isinstance(value, list):
        value = next((x for x in value if isinstance(x, str)), None)
    if isinstance(value, dict):
        value = value.get("url")
    if type(value) is not str or len(value) > 2048:
        return None
    try:
        parts = urlsplit(value)
        if (parts.scheme != "https" or not parts.hostname
                or parts.username or parts.password
                or parts.port not in (None, 443)
                or parts.fragment or parts.query):
            return None
    except ValueError:
        return None
    # The metadata is informational only; do not fetch arbitrary image hosts.
    return value if parts.hostname == "www.biblioaccess.com" else None


class _Metadata(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.script = False
        self.script_data = []
        self.script_len = 0
        self.structured = []
        self.meta = {}
        self.overflow = False

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            values = dict(attrs)
            self.script = (str(values.get("type") or "").strip().lower() ==
                           "application/ld+json")
            self.script_data = []
            self.script_len = 0
        if tag == "meta":
            values = dict(attrs)
            key = values.get("property") or values.get("name")
            text = _short(values.get("content"))
            if key in ("og:title", "og:image") and text:
                self.meta.setdefault(key, text)

    def handle_data(self, data):
        if self.script:
            self.script_len += len(data)
            if self.script_len > MAX_SCRIPT_BYTES:
                self.overflow = True
                self.script = False
                self.script_data = []
            else:
                self.script_data.append(data)

    def handle_endtag(self, tag):
        if tag != "script" or not self.script:
            return
        self.script = False
        try:
            parsed = json.loads("".join(self.script_data))
        except (ValueError, TypeError):
            return
        # Prevent unbounded nested graph traversal or recursive expansion.
        nodes = parsed if type(parsed) is list else [parsed]
        for node in nodes[:30]:
            if type(node) is not dict:
                continue
            self.structured.append(node)
            graph = node.get("@graph")
            if type(graph) is list:
                self.structured.extend(
                    item for item in graph[:30] if type(item) is dict
                )


def parse_book_metadata(page_url: str, html: str) -> BibliographicNotice | None:
    book_id = book_id_from_url(page_url)
    if book_id is None:
        raise ValueError("biblioaccess_invalid_book_url")
    if type(html) is not str or len(html.encode("utf-8")) > MAX_HTML_BYTES:
        raise ValueError("biblioaccess_invalid_html")
    parser = _Metadata()
    parser.feed(html)
    parser.close()
    if parser.overflow:
        return None  # Don't accept truncated structured-data evidence.
    candidates = []
    for entry in parser.structured[:60]:
        categories = entry.get("@type")
        kinds = categories if isinstance(categories, list) else [categories]
        if not any(kind in ("Book", "https://schema.org/Book", "http://schema.org/Book")
                   for kind in kinds):
            continue
        title = _short(entry.get("name") or entry.get("headline"))
        if not title:
            continue
        item_url = entry.get("url")
        if item_url is not None and (
                type(item_url) is not str
                or book_id_from_url(item_url) != book_id):
            continue
        candidates.append((entry, title, item_url is not None))
    # A page with several unrelated Book objects is ambiguous without
    # an exact canonical URL match; never attach a recommendation to the page.
    exact = [row for row in candidates if row[2]]
    if len(exact) == 1:
        selected = exact[0]
    elif not exact and len(candidates) == 1:
        selected = candidates[0]
    else:
        selected = None
    if selected is not None:
        entry, title, _ = selected
        return BibliographicNotice(
            book_id=book_id, title=title,
            author=_name(entry.get("author")),
            publisher=_name(entry.get("publisher")),
            isbn13=_isbn(entry.get("isbn")),
            published_at=_short(entry.get("datePublished")),
            image_url=_image(entry.get("image")),
        )
    # OpenGraph alone is not sufficient for a typed Book notice.
    return None
