"""Safe, local-only projection of known book metadata into compact detail rows.

Do not render arbitrary provider mappings.  A key must be explicitly allowed,
and values containing URL-like credentials, paths or control characters are
never passed to the UI.  Filtering is conservative by design.
"""
from __future__ import annotations

from collections.abc import Mapping

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
    ("doi", "DOI déclaré"),
    ("ean", "EAN déclaré"),
    ("price", "Prix éditeur"),
)
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


def detail_metadata_rows(metadata: object) -> tuple[tuple[str, str], ...]:
    """Return explicit bibliographic facts only; no network, HTML or raw dict."""
    if not isinstance(metadata, Mapping):
        return ()
    rows: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    omitted = 0
    for prefix, data in (
        ("", metadata),
        ("Catalogue · ", metadata.get("catalogue_metadata")),
        ("EPUB · ", metadata.get("epub_metadata")),
    ):
        if not isinstance(data, Mapping):
            continue
        for key, label in _FIELDS:
            raw = data.get(key)
            for value in _values(raw, key):
                row = (prefix + label, value)
                if row in seen:
                    continue
                seen.add(row)
                if len(rows) < _MAX_ROWS:
                    rows.append(row)
                else:
                    omitted += 1
    if omitted:
        rows.append(("Métadonnées", f"{omitted} valeur(s) supplémentaires non affichées (limite de sécurité)"))
    return tuple(rows)
