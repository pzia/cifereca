"""Offline, bounded JSON-LD evidence extraction from an anonymous Numilog product page.

This module does not fetch pages, authenticate, claim ownership or interpret
JSON-LD offers as acquisition rights. Callers must attest the HTML provenance.
"""
from __future__ import annotations

from html.parser import HTMLParser
from datetime import date
import json
import re
import unicodedata
from urllib.parse import urlsplit

from cifereca_numilog_html import public_book_url

MAX_DETAIL_BYTES = 1024 * 1024
MAX_JSONLD_BYTES = 128 * 1024
MAX_SCRIPTS = 12


class PublicDetailError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__("Numilog public detail: " + code)


def _valid_text(value, limit):
    return (type(value) is str and 0 < len(value.strip()) <= limit
            and not any(unicodedata.category(c) in {"Cc", "Cf", "Cs"} for c in value))


class _Scripts(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.scripts = []
        self.active = False
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag != "script":
            return
        attributes = dict(attrs)
        declared = attributes.get("type")
        kind = (declared or "").split(";", 1)[0].strip().lower() if isinstance(declared, (str, type(None))) else ""
        if kind == "application/ld+json":
            if self.active or len(self.scripts) >= MAX_SCRIPTS:
                raise PublicDetailError("too_many_jsonld_scripts")
            self.active = True
            self.parts = []

    def handle_data(self, data):
        if self.active:
            self.parts.append(data)
            if sum(len(part) for part in self.parts) > MAX_JSONLD_BYTES:
                raise PublicDetailError("jsonld_too_large")

    def handle_endtag(self, tag):
        if tag == "script" and self.active:
            self.scripts.append("".join(self.parts))
            self.parts = []
            self.active = False


def _nodes(obj, depth=0):
    if depth > 8:
        raise PublicDetailError("jsonld_too_deep")
    if isinstance(obj, list):
        if len(obj) > 100:
            raise PublicDetailError("jsonld_too_many_nodes")
        for entry in obj:
            yield from _nodes(entry, depth + 1)
    elif isinstance(obj, dict):
        yield obj
        if "@graph" in obj:
            yield from _nodes(obj["@graph"], depth + 1)


def _book_type(node):
    values = node.get("@type", [])
    if isinstance(values, str):
        values = [values]
    return isinstance(values, list) and any(
        isinstance(v, str) and v in {"Book", "https://schema.org/Book", "http://schema.org/Book"}
        for v in values
    )


def _isbn(value):
    if not _valid_text(value, 32):
        return None
    digits = re.sub(r"[ -]", "", value)
    if not re.fullmatch(r"[0-9]{13}", digits):
        return None
    numbers = [int(c) for c in digits]
    checksum = (10 - sum(n * (1 if i % 2 == 0 else 3)
                         for i, n in enumerate(numbers[:12])) % 10) % 10
    return digits if checksum == numbers[12] else None


def parse_public_detail_jsonld(html, product_url):
    """Return observed field values and evidence, without guessing edition rights.

    The JSON-LD Book must explicitly point to the exact supplied canonical
    product URL; ambiguity and any conflicting Book nodes fail closed.
    ISBN is kept as an *unspecified edition* identifier until independently
    proven digital; it MUST NOT be used to match purchased digital editions.
    """
    if (public_book_url(product_url, base_url="https://www.numilog.com/") != product_url
            or urlsplit(product_url).scheme != "https"
            or urlsplit(product_url).netloc != "www.numilog.com"):
        raise PublicDetailError("invalid_product_url")
    if (type(html) is not str or len(html.encode("utf-8", "replace")) > MAX_DETAIL_BYTES):
        raise PublicDetailError("html_too_large")
    reader = _Scripts()
    try:
        reader.feed(html)
        reader.close()
    except (ValueError, OverflowError):
        raise PublicDetailError("invalid_html") from None
    matches = []
    for script in reader.scripts:
        try:
            parsed = json.loads(script)
        except (ValueError, TypeError, RecursionError):
            raise PublicDetailError("invalid_jsonld") from None
        for node in _nodes(parsed):
            if not _book_type(node):
                continue
            raw_url = node.get("url")
            if not _valid_text(raw_url, 512) or raw_url != product_url:
                continue
            matches.append(node)
    if len(matches) != 1:
        raise PublicDetailError("missing_or_ambiguous_book")
    node = matches[0]
    fields = {}
    for key, limit in (("name", 512), ("description", 16000),
                       ("inLanguage", 80), ("datePublished", 32)):
        value = node.get(key)
        if _valid_text(value, limit):
            fields[{"name": "title", "inLanguage": "language",
                    "datePublished": "published_at"}.get(key, key)] = value.strip()
    # Normalized release dates must be valid; never persist arbitrary strings
    # from an unqualified JSON-LD field into public catalogue dates.
    release_date = fields.get("published_at")
    if release_date:
        try:
            if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", release_date):
                raise ValueError
            date.fromisoformat(release_date)
        except ValueError:
            fields.pop("published_at")
    observed_isbn = _isbn(node.get("isbn"))
    if observed_isbn:
        fields["isbn_edition_unspecified"] = observed_isbn
    authors = node.get("author")
    if isinstance(authors, (str, dict)):
        authors = [authors]
    if isinstance(authors, list) and len(authors) <= 32:
        names = []
        for author in authors:
            raw = author.get("name") if isinstance(author, dict) else author
            if not _valid_text(raw, 256):
                names = []
                break
            names.append(raw.strip())
        if names:
            fields["authors"] = names
    publisher = node.get("publisher")
    if isinstance(publisher, dict):
        publisher = publisher.get("name")
    if _valid_text(publisher, 256):
        fields["publisher"] = publisher.strip()
    return {
        "product_url": product_url,
        "fields": fields,
        "field_evidence": {
            key: {"source": "public_product_jsonld_Book", "source_url": product_url,
                  "kind": "observed_public_jsonld", "edition_proven": False}
            for key in fields
        },
        "owned": False,
        "downloadable": False,
        "download_url": None,
    }


def validate_public_detail_item(item):
    """Check source, account boundary, product identity and search provenance."""
    from cifereca.plugin_api import ProviderCapabilityError
    from cifereca_numilog_public import _query

    error = "Numilog public : detail_evidence_conflict"
    url = getattr(item, "friendly_url", None)
    metadata = getattr(item, "metadata", None)
    if (type(url) is not str
            or public_book_url(url, base_url="https://www.numilog.com/") != url
            or type(metadata) is not dict
            or getattr(item, "source_provider", None) != "numilog-catalog"
            or getattr(item, "source_credential", None) != "__public__"
            or getattr(item, "source_collection", None) != "public_catalog"
            or getattr(item, "owned", None) is not False
            or getattr(item, "downloadable", None) is not False
            or getattr(item, "download_url", None) is not None):
        raise ProviderCapabilityError(error)
    product_id = urlsplit(url).path.split("/")[1]
    if (getattr(item, "record_id", None) != "numilog:public:" + product_id
            or metadata.get("source_item_id") != product_id):
        raise ProviderCapabilityError(error)
    try:
        _query(metadata.get("source_url"))
    except Exception:
        raise ProviderCapabilityError(error) from None
    return url


def merge_public_detail(item, detail):
    """Opt-in projection of a public Book JSON-LD onto a catalogue result.

    Never use for purchased rows. Existing card values take precedence and
    disagreements remain visible. ISBN is never promoted to a digital ISBN.
    """
    from dataclasses import replace
    from cifereca.plugin_api import ProviderCapabilityError

    error = "Numilog public : detail_evidence_conflict"
    url = validate_public_detail_item(item)
    metadata = item.metadata
    if (type(detail) is not dict or detail.get("product_url") != url
            or detail.get("owned") is not False
            or detail.get("downloadable") is not False
            or detail.get("download_url", False) is not None
            or type(detail.get("fields")) is not dict
            or type(detail.get("field_evidence")) is not dict):
        raise ProviderCapabilityError(error)
    fields = detail["fields"]
    evidence = detail["field_evidence"]
    allowed = {
        "title", "description", "language", "published_at",
        "isbn_edition_unspecified", "authors", "publisher",
    }
    if not set(fields).issubset(allowed) or set(evidence) != set(fields):
        raise ProviderCapabilityError(error)
    for key in fields:
        proof = evidence[key]
        if (type(proof) is not dict
                or proof.get("source_url") != url
                or proof.get("kind") != "observed_public_jsonld"
                or proof.get("edition_proven") is not False):
            raise ProviderCapabilityError(error)

    projection = {}
    conflicts = []
    for key, target in (
        ("description", "description"), ("published_at", "published_at"),
        ("publisher", "publisher"), ("authors", "author"),
    ):
        value = fields.get(key)
        if key == "authors":
            if not isinstance(value, list) or not value:
                continue
            value = " ; ".join(value)
        if not isinstance(value, str) or not value:
            continue
        original = getattr(item, target, None)
        if original:
            if original.strip() != value.strip():
                conflicts.append(key)
        else:
            projection[target] = value
    if fields.get("title") and fields["title"].strip() != item.title.strip():
        conflicts.append("title")
    # A page declaring a different book at the same URL cannot safely fill
    # missing listing fields; expose conflict without projecting new values.
    if "title" in conflicts:
        projection.clear()
    next_meta = dict(metadata)
    next_meta["public_detail"] = {
        "status": "observed_jsonld",
        "source_url": url,
        "fields": dict(fields),
        "field_evidence": dict(evidence),
        "listing_conflicts": tuple(conflicts),
        "edition_match": "canonical_product_url_only",
        "rights_proven": False,
    }
    return replace(item, metadata=next_meta, **projection)
