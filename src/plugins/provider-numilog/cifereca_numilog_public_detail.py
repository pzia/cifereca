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


# HTML void elements cannot establish hidden ancestry for a later script.
_VOID_ELEMENTS = frozenset({
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
})
_INERT_ANCESTORS = frozenset({"template", "noscript", "iframe", "svg"})


class _Scripts(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.scripts = []
        self.active = False
        self.parts = []
        self.parts_bytes = 0
        self._elements = []
        self._hidden_depth = 0
        self._open_tags = {}

    @staticmethod
    def _invisible(tag, attrs):
        if tag in _INERT_ANCESTORS:
            return True
        # An HTML attribute list can contain duplicates. Any explicit
        # non-evidence marker wins rather than allowing a later attribute
        # to silently undo the first one through dict conversion.
        if any(key == "hidden" for key, _ in attrs):
            return True
        if any(key == "aria-hidden" and isinstance(value, str)
               and value.strip().casefold() == "true" for key, value in attrs):
            return True
        styles = [str(value or "").casefold() for key, value in attrs
                  if key == "style"]
        for style in styles:
            compact = "".join(style.split())
            if "display:none" in compact or "visibility:hidden" in compact:
                return True
        return False

    def handle_starttag(self, tag, attrs):
        invisible = self._invisible(tag, attrs)
        if tag not in _VOID_ELEMENTS:
            # A hostile 1 MiB HTML document can contain 100k nested tags.
            # Bound the ancestry and reverse-scan cost independently of bytes.
            if len(self._elements) >= 512:
                raise PublicDetailError("html_too_deep")
            self._elements.append((tag, invisible))
            self._open_tags[tag] = self._open_tags.get(tag, 0) + 1
            if invisible:
                self._hidden_depth += 1
        # Only scripts in an active, unhidden HTML ancestor chain are
        # evidence for the visible public product. Ordinary head JSON-LD
        # remains eligible, unlike inert template/fallback descendants.
        if tag != "script" or self._hidden_depth:
            return
        if sum(key == "type" for key, _value in attrs) > 1:
            raise PublicDetailError("ambiguous_jsonld_type")
        attributes = dict(attrs)
        declared = attributes.get("type")
        kind = (declared or "").split(";", 1)[0].strip().lower() if isinstance(declared, (str, type(None))) else ""
        if kind == "application/ld+json":
            if self.active or len(self.scripts) >= MAX_SCRIPTS:
                raise PublicDetailError("too_many_jsonld_scripts")
            self.active = True
            self.parts = []
            self.parts_bytes = 0

    def handle_data(self, data):
        if self.active:
            # Count actual encoded bytes once per chunk: the previous
            # sum(parts) loop had quadratic runtime on fragmented scripts.
            self.parts_bytes += len(data.encode("utf-8", "replace"))
            if self.parts_bytes > MAX_JSONLD_BYTES:
                raise PublicDetailError("jsonld_too_large")
            self.parts.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self.active:
            self.scripts.append("".join(self.parts))
            self.parts = []
            self.active = False
        # HTMLParser does not supply ancestor state. Unwind up to the
        # closest matching open tag (including intervening malformed
        # descendants), without letting an unrelated close unblock a
        # hidden product/script prematurely.
        if not self._open_tags.get(tag):
            return  # Ignore unmatched end tags in O(1).
        for index in range(len(self._elements) - 1, -1, -1):
            if self._elements[index][0] == tag:
                for name, invisible in self._elements[index:]:
                    if invisible:
                        self._hidden_depth -= 1
                    count = self._open_tags[name] - 1
                    if count:
                        self._open_tags[name] = count
                    else:
                        del self._open_tags[name]
                del self._elements[index:]
                break


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

    The JSON-LD Book (or observed Product explicitly typed Book with matching
    sku) must explicitly point to the exact supplied canonical
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
    except PublicDetailError:
        # Stable public error codes are part of the parser contract.
        # Do not fold bounds violations into a generic invalid_html.
        raise
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
                # Observed anonymous Numilog pages describe a Product whose
                # additionalType is Book. A generic Product is insufficient;
                # require its explicit book type and exact canonical product ID.
                product_id = urlsplit(product_url).path.split('/')[1]
                if not (
                    node.get('@type') == 'Product'
                    and re.fullmatch(r'[0-9]{1,20}', product_id) is not None
                    and type(node.get('additionalType')) is str
                    and node['additionalType'] in {
                        'Book', 'https://schema.org/Book', 'http://schema.org/Book',
                    }
                    and type(node.get('sku')) is str
                    and node['sku'] == product_id
                ):
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
    # Official product JSON-LD may publish only gtin13. Preserve the raw
    # *typed GTIN fact*, not a purchased EPUB/PDF ISBN or media/rights proof.
    # If isbn and gtin13 disagree they remain two distinct public facts.
    observed_gtin13 = _isbn(node.get("gtin13"))
    if observed_gtin13:
        fields["public_gtin13_edition_unspecified"] = observed_gtin13
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

    # Schema.org Book/CreativeWork roles are explicit properties, NOT
    # interchangeable authors. Accept only named Person/Organization objects;
    # plain strings or mixed/ambiguous lists do not establish a contributor
    # identity or role. Roles stay public editorial facts, not owned rights.
    roles = (
        ("translator", "translators", frozenset(("Person", "Organization"))),
        ("illustrator", "illustrators", frozenset(("Person",))),
        ("contributor", "contributors", frozenset(("Person", "Organization"))),
    )
    for source_key, target, eligible_types in roles:
        observed = node.get(source_key)
        if type(observed) is dict:
            observed = [observed]
        if type(observed) is not list or not 1 <= len(observed) <= 16:
            continue
        names = []
        for contributor in observed:
            if type(contributor) is not dict:
                names = []
                break
            kind = contributor.get("@type")
            allowed_types = (
                eligible_types
                | {"https://schema.org/" + item for item in eligible_types}
                | {"http://schema.org/" + item for item in eligible_types}
            )
            # An untyped {"name": "..."} object is not evidence of a
            # Person/Organization role. Never infer contributor identity from
            # its name or from an incidental occurrence in Book JSON-LD.
            if type(kind) is not str or kind not in allowed_types:
                names = []
                break
            name = contributor.get("name")
            if not _valid_text(name, 256):
                names = []
                break
            names.append(name.strip())
        if names:
            fields[target] = list(dict.fromkeys(names))

    # Public Book declarations do not identify the purchased file format.
    # Keep edition/pagination/age as literal, provenance-scoped metadata:
    # never turn an age label into recommended_age or a format entitlement.
    for source_key, target, maximum in (
        ("bookEdition", "public_edition_label", 256),
        ("typicalAgeRange", "public_age_range_label", 80),
    ):
        value = node.get(source_key)
        if _valid_text(value, maximum):
            fields[target] = value.strip()
    pages = node.get("numberOfPages")
    if type(pages) is int and 1 <= pages <= 20000:
        fields["public_page_count"] = pages

    # Explicit Schema.org Book editorial declarations. A commercial genre,
    # search keyword or series name is NEVER a purchased-edition identity,
    # ISBN, verified shelving classification or right to an EPUB/PDF file.
    # The exact declared literals (including comma-separated keywords) stay
    # intact; no heuristic tokenization, translation or taxonomy mapping.
    for source_key, destination in (
        ("genre", "genres"), ("keywords", "public_keywords"),
    ):
        values = node.get(source_key)
        if type(values) is str:
            values = [values]
        if (type(values) is list and 1 <= len(values) <= 16
                and all(_valid_text(label, 256)
                        and not label.strip().casefold().startswith(
                            ("http://", "https://", "javascript:", "data:")
                        ) for label in values)):
            fields[destination] = list(dict.fromkeys(v.strip() for v in values))

    headline = node.get("alternativeHeadline")
    if (_valid_text(headline, 256) and not headline.strip().casefold().startswith(
            ("http://", "https://", "javascript:", "data:"))):
        # alternativeHeadline is not automatically the book's subtitle.
        fields["public_alternative_headline"] = headline.strip()

    series = node.get("isPartOf")
    if type(series) is dict:
        kind, series_name = series.get("@type"), series.get("name")
        if (type(kind) is str and kind in {
                "BookSeries", "CreativeWorkSeries",
                "https://schema.org/BookSeries",
                "http://schema.org/BookSeries",
                "https://schema.org/CreativeWorkSeries",
                "http://schema.org/CreativeWorkSeries",
            } and _valid_text(series_name, 256)
                and not series_name.strip().casefold().startswith(
                    ("http://", "https://", "javascript:", "data:"))):
            fields["public_series_label"] = series_name.strip()
    from cifereca_numilog_public_identifiers import (
        PublicIdentifierError, extract_public_identifiers,
    )
    try:
        typed_identifiers = extract_public_identifiers(
            html, product_url, expected_title=fields.get("title"),
            require_product_form=True,
        )
    except PublicIdentifierError:
        raise PublicDetailError("public_identifier_evidence_unverified") from None
    from cifereca_numilog_public_metadata import extract_public_metadata, PublicMetadataError
    try:
        visible_metadata = extract_public_metadata(html, product_url, fields.get("title"))
    except PublicMetadataError:
        raise PublicDetailError("public_metadata_evidence_unverified") from None
    from cifereca_numilog_public_commercial import extract_public_commercial
    public_commercial = extract_public_commercial(node, product_url)
    return {
        "public_commercial": public_commercial,
        "public_html_metadata": visible_metadata,
        "product_url": product_url,
        "public_identifiers": typed_identifiers["identifiers"],
        "identifier_evidence": typed_identifiers["identifier_evidence"],
        "fields": fields,
        "field_evidence": {
            key: {"source": ("public_product_jsonld_Book" if _book_type(node)
                             else "public_product_jsonld_Product_Book"), "source_url": product_url,
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
            or getattr(item, "scenario", None) != "NUMILOG_PUBLIC"
            or getattr(item, "docbase", None) != "NUMILOG"
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


def validated_public_jsonld_fields(detail, product_url):
    """Re-qualify every JSON-LD field at the Core/catalogue merge boundary.

    A parsed object may pass through a cache, plugin extension or caller before
    merging. Never copy arbitrary nested vendor values or trust a spoofed
    source label merely because its URL, kind and edition flag look correct.
    """
    if (type(detail) is not dict or type(detail.get("fields")) is not dict
            or type(detail.get("field_evidence")) is not dict):
        raise PublicDetailError("public_jsonld_evidence_unverified")
    fields = detail["fields"]
    evidence = detail["field_evidence"]
    allowed = {
        "title": 512, "description": 16000, "language": 80,
        "published_at": 32, "isbn_edition_unspecified": 32,
        "public_gtin13_edition_unspecified": 13,
        "authors": 32, "publisher": 256,
        "translators": 16, "illustrators": 16, "contributors": 16,
        "public_edition_label": 256, "public_age_range_label": 80,
        "public_page_count": 20000,
        "genres": 16, "public_keywords": 16,
        "public_alternative_headline": 256,
        "public_series_label": 256,
    }
    if (not set(fields).issubset(allowed)
            or set(fields) != set(evidence)):
        raise PublicDetailError("public_jsonld_evidence_unverified")
    result, proofs = {}, {}
    for key, value in fields.items():
        if key in {"authors", "translators", "illustrators", "contributors",
                   "genres", "public_keywords"}:
            ceiling = 32 if key == "authors" else 16
            if (type(value) is not list or not 1 <= len(value) <= ceiling
                    or any(not _valid_text(name, 256) or name != name.strip()
                           for name in value)
                    or (key != "authors" and len(set(value)) != len(value))
                    or (key in {"genres", "public_keywords"} and any(
                        name.casefold().startswith(
                            ("http://", "https://", "javascript:", "data:")
                        ) for name in value))):
                raise PublicDetailError("public_jsonld_evidence_unverified")
            qualified = list(value)
        elif key == "public_page_count":
            if type(value) is not int or not 1 <= value <= allowed[key]:
                raise PublicDetailError("public_jsonld_evidence_unverified")
            qualified = value
        elif key in {"public_series_label", "public_alternative_headline"}:
            if (not _valid_text(value, allowed[key]) or value != value.strip()
                    or value.casefold().startswith(
                        ("http://", "https://", "javascript:", "data:"))):
                raise PublicDetailError("public_jsonld_evidence_unverified")
            qualified = value
        elif key in {"isbn_edition_unspecified", "public_gtin13_edition_unspecified"}:
            if type(value) is not str or _isbn(value) != value:
                raise PublicDetailError("public_jsonld_evidence_unverified")
            qualified = value
        else:
            if not _valid_text(value, allowed[key]):
                raise PublicDetailError("public_jsonld_evidence_unverified")
            qualified = value
            if key == "published_at":
                try:
                    if (not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value)
                            or date.fromisoformat(value).isoformat() != value):
                        raise ValueError
                except ValueError:
                    raise PublicDetailError("public_jsonld_evidence_unverified") from None
        proof = evidence[key]
        if (type(proof) is not dict or proof.get("edition_proven") is not False
                or proof not in (
                {"source": "public_product_jsonld_Book",
                 "source_url": product_url, "kind": "observed_public_jsonld",
                 "edition_proven": False},
                {"source": "public_product_jsonld_Product_Book",
                 "source_url": product_url, "kind": "observed_public_jsonld",
                 "edition_proven": False},
        )):
            raise PublicDetailError("public_jsonld_evidence_unverified")
        result[key] = qualified
        proofs[key] = dict(proof)
    return result, proofs


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
    from cifereca_numilog_public_identifiers import (
        PublicIdentifierError, validated_public_identifiers,
    )
    try:
        fields, evidence = validated_public_jsonld_fields(detail, url)
        public_ids, public_id_evidence = validated_public_identifiers(detail, url)
    except (PublicDetailError, PublicIdentifierError):
        raise ProviderCapabilityError(error) from None

    # Product URL equality alone does not guarantee that the currently
    # published product JSON-LD still describes the searched book. Reject
    # contradictory *or absent* Book titles before copying any identifiers,
    # commercial offers, publisher, formats or cover into this catalogue row.
    # Unlike displaying a conflict marker beside the listing, preserving
    # those foreign fields in metadata would silently misattribute an ISBN.
    def _title_key(value):
        if not _valid_text(value, 512):
            return None
        return unicodedata.normalize(
            "NFKC", " ".join(value.split()),
        ).casefold()

    listed_title = _title_key(getattr(item, "title", None))
    detail_title = _title_key(fields.get("title"))
    if (listed_title is None or detail_title is None
            or listed_title != detail_title):
        raise ProviderCapabilityError("Numilog public : detail_title_conflict")

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
    from cifereca_numilog_public_metadata import validated_public_metadata, PublicMetadataError
    from cifereca_numilog_public_commercial import (
        validated_public_commercial, PublicCommercialError,
    )
    try:
        html_metadata = validated_public_metadata(detail, url)
        commercial = validated_public_commercial(detail, url)
    except (PublicMetadataError, PublicCommercialError):
        raise ProviderCapabilityError(error) from None
    next_meta = dict(metadata)
    next_meta["public_detail"] = {
        "status": "observed_jsonld",
        "source_url": url,
        "fields": dict(fields),
        "field_evidence": dict(evidence),
        "listing_conflicts": tuple(conflicts),
        "edition_match": "canonical_product_url_only",
        "rights_proven": False,
        "public_identifiers": public_ids,
        "identifier_evidence": public_id_evidence,
        "format_edition_proven": False,
    }
    if commercial is not None:
        # Store-only evidence: not a downloaded or purchased edition's price,
        # stock, cover or offer. Never overwrite search price or image.
        next_meta["public_detail"]["public_commercial"] = commercial
    if html_metadata is not None:
        next_meta["public_detail"]["public_html_metadata"] = html_metadata
        next_meta["public_product_metadata"] = dict(html_metadata["fields"])
    return replace(item, metadata=next_meta, **projection)
