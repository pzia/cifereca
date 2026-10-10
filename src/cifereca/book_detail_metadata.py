"""Safe, local-only projection of known book metadata into compact detail rows.

Do not render arbitrary provider mappings.  A key must be explicitly allowed,
and values containing URL-like credentials, paths or control characters are
never passed to the UI.  Filtering is conservative by design.
"""
from __future__ import annotations

from collections.abc import Mapping
from itertools import chain
import re

# These are *public bibliographic facts*, not provider-internal identities.
_FIELDS = (
    ("subtitle", "Sous-titre"),
    ("alternate_titles", "Titre alternatif"),
    ("authors", "Auteur"),
    ("author", "Auteur"),
    ("contributors", "Contributeur"),
    ("publisher", "Éditeur"),
    ("publishers", "Éditeur"),
    ("published_at", "Publication"),
    ("publication_date", "Date de publication"),
    ("publication_year", "Année de publication"),
    ("dates", "Date"),
    ("edition", "Édition"),
    ("series", "Série"),
    ("collection", "Collection"),
    ("volume", "Tome"),
    ("subjects", "Sujet"),
    ("themes", "Thème"),
    ("genres", "Genre"),
    ("languages", "Langue"),
    ("language", "Langue"),
    ("formats", "Format"),
    ("format", "Format"),
    ("book_type", "Type"),
    ("pages", "Pages"),
    ("page_count", "Pages"),
    ("number_of_pages", "Pages"),
    ("rights", "Droits"),
    ("license", "Licence"),
    ("isbn", "ISBN déclaré"),
    ("isbns", "ISBN déclaré"),
    ("isbn13", "ISBN déclaré"),
    ("isbn10", "ISBN déclaré"),
    ("doi", "DOI déclaré"),
    ("ean", "EAN déclaré"),
    ("price", "Prix éditeur"),
)
_FIELD_KIND = {
    "subtitle": "title", "alternate_titles": "title",
    "authors": "author", "author": "author", "contributors": "author",
    "subjects": "subject", "themes": "subject", "genres": "subject",
    "series": "series", "collection": "collection",
    "languages": "language", "language": "language",
    "isbn": "isbn", "isbns": "isbn", "isbn13": "isbn", "isbn10": "isbn",
}
_COPY_ONLY = {"pages", "page_count", "number_of_pages", "publication_year",
              "dates", "published_at", "publication_date", "price"}


def canonical_book_isbn(value: object) -> str:
    """Canonical Bookland ISBN only (not arbitrary EAN or invalid checksum)."""
    raw = re.sub(r"[^0-9X]", "", str(value).upper())
    if len(raw) == 10 and re.fullmatch(r"[0-9]{9}[0-9X]", raw):
        check = sum((10 - i) * int(ch) for i, ch in enumerate(raw[:9]))
        check += 10 if raw[-1] == "X" else int(raw[-1])
        if check % 11 != 0:
            return ""
        raw = "978" + raw[:9]
        raw += str((10 - sum(int(ch) * (1 if i % 2 == 0 else 3)
                             for i, ch in enumerate(raw)) % 10) % 10)
    if len(raw) != 13 or not raw.startswith(("978", "979")):
        return ""
    expected = (10 - sum(int(ch) * (1 if i % 2 == 0 else 3)
                         for i, ch in enumerate(raw[:12])) % 10) % 10
    return raw if expected == int(raw[-1]) else ""


# Limits are visible when reached, never a silent [:N] truncation.
_MAX_ROWS = 200
_MAX_VALUE_LENGTH = 4096
_MAX_COLLECTION_ENTRIES = 500


def _safe_text(value: object) -> str:
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        return ""
    result = str(value).strip()
    if not result or result in {"—", "Inconnu"}:
        return ""
    lower = result.casefold()
    if any(token in lower for token in (
        "://", "bearer ", "authorization:", "cookie:", "token=",
        "password=", "secret=", "/home/", "/users/", "\\x00",
    )):
        return ""
    if any(ord(char) < 32 and char not in "\n\t" for char in result):
        return ""
    if len(result) > _MAX_VALUE_LENGTH:
        return f"Valeur trop longue ({len(result)} caractères) — non affichée"
    return result


def _values(raw: object, key: str):
    if isinstance(raw, Mapping):
        if key == "contributors":
            raw = [str(row.get("name", "")) + (
                " · " + str(row.get("role")) if row.get("role") else ""
            ) for row in (raw,) if row.get("name")]
        elif key == "series":
            raw = [str(raw.get("name", "")) + (
                " · tome " + str(raw.get("index")) if raw.get("index") else ""
            )] if raw.get("name") else []
        else:
            return
    if isinstance(raw, (tuple, list)):
        for index, value in enumerate(raw):
            if index >= _MAX_COLLECTION_ENTRIES:
                yield "Liste longue : valeurs supplémentaires non affichées"
                break
            yield from _values(value, key)
    else:
        value = _safe_text(raw)
        if value:
            yield value


def _identifier_items(raw: object):
    """Known public bibliographic IDs, never arbitrary provider identifiers.

    EPUB records contain [{"scheme": "ISBN", "value": "..."}]. Other
    catalogues sometimes expose {"isbn": "..."} instead. In both forms
    only schemes on this allowlist cross the GTK metadata boundary.
    """
    if isinstance(raw, Mapping):
        if "scheme" in raw or "value" in raw:
            rows = (raw,)
        else:
            rows = ({"scheme": key, "value": value} for key, value in raw.items())
    elif isinstance(raw, (list, tuple)):
        rows = raw
    else:
        return
    for index, record in enumerate(rows):
        if index >= _MAX_COLLECTION_ENTRIES:
            break
        if not isinstance(record, Mapping):
            continue
        scheme = _safe_text(record.get("scheme") or record.get("type"))
        value = _safe_text(record.get("value"))
        if not scheme or not value or value.startswith("Valeur trop longue ("):
            continue
        normalized = re.sub(r"[^a-z0-9]", "", scheme.casefold())
        if normalized in {"isbn", "isbn10", "isbn13"}:
            yield "ISBN déclaré", value, "isbn"
        elif normalized == "doi":
            yield "DOI déclaré", value, "all"
        elif normalized in {"ean", "ean13"}:
            yield "EAN déclaré", value, "all"
        elif normalized == "issn":
            yield "ISSN déclaré", value, "all"
        elif normalized == "oclc":
            yield "OCLC déclaré", value, "all"


def detail_metadata_items(metadata: object) -> tuple[tuple[str, str, str], ...]:
    """Return explicit bibliographic facts only; no network, HTML or raw dict."""
    if not isinstance(metadata, Mapping):
        return ()
    rows: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    omitted = 0
    for prefix, data in (
        ("", metadata),
        ("Catalogue · ", metadata.get("catalogue_metadata")),
        ("EPUB · ", metadata.get("epub_metadata")),
    ):
        if not isinstance(data, Mapping):
            continue
        known = (
            (label, value, _FIELD_KIND.get(
                key, "copy" if key in _COPY_ONLY else "all",
            ))
            for key, label in _FIELDS
            for value in _values(data.get(key), key)
        )
        for label, value, kind in chain(
            known, _identifier_items(data.get("identifiers")),
        ):
            actual_label = prefix + label
            if kind == "isbn":
                checked = canonical_book_isbn(value)
                if checked:
                    value = checked
                else:
                    actual_label, kind = prefix + "Identifiant déclaré", "all"
            row = (actual_label, value, kind)
            key_for_dedup = (actual_label, value)
            if key_for_dedup in seen:
                continue
            seen.add(key_for_dedup)
            if len(rows) < _MAX_ROWS:
                rows.append(row)
            else:
                omitted += 1
    if omitted:
        rows.append(("Métadonnées", f"{omitted} valeur(s) supplémentaires non affichées (limite de sécurité)", "copy"))
    return tuple(rows)


def detail_metadata_rows(metadata: object) -> tuple[tuple[str, str], ...]:
    """Compatibility shape for existing consumers."""
    return tuple((label, value) for label, value, _kind in detail_metadata_items(metadata))


def safe_detail_value(value: object) -> str:
    """UI boundary for copying/searching a single known public detail value."""
    result = _safe_text(value)
    if result.startswith("Valeur trop longue ("):
        return ""
    return result
