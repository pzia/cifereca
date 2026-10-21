"""Safe, local-only projection of known book metadata into compact detail rows.

Do not render arbitrary provider mappings.  A key must be explicitly allowed,
and values containing URL-like credentials, paths or control characters are
never passed to the UI.  Filtering is conservative by design.
"""
from __future__ import annotations

from collections.abc import Mapping
from itertools import chain
import re
from .descriptions import description_text

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
    ("edition_statement", "Édition"),
    ("series", "Série"),
    ("series_index", "Numéro dans la série"),
    ("collection", "Collection"),
    ("collections", "Collection"),
    ("collection_index", "Numéro dans la collection"),
    ("volume", "Tome"),
    # Keep short editorial themes visible even when a provider supplies
    # hundreds of subject headings; the overall projection remains bounded.
    ("themes", "Thème"),
    ("keywords", "Mot-clé"),
    ("tags", "Mot-clé"),
    ("awards", "Prix littéraire"),
    ("prizes", "Prix littéraire"),
    ("literary_prizes", "Prix littéraire"),
    ("subjects", "Sujet"),
    ("subject", "Sujet"),
    ("genres", "Genre"),
    ("genre", "Genre"),
    ("categories", "Catégorie"),
    ("languages", "Langue"),
    ("language", "Langue"),
    ("original_language", "Langue originale"),
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
    ("description", "Résumé"),
    ("summary", "Résumé"),
    ("synopsis", "Résumé"),
)
_FIELD_KIND = {
    "subtitle": "title", "alternate_titles": "title",
    "authors": "author", "author": "author", "contributors": "author",
    "subjects": "subject", "subject": "subject", "themes": "subject", "genres": "subject",
    "genre": "subject", "keywords": "subject", "tags": "subject",
    "series": "series", "collection": "collection", "collections": "collection",
    "languages": "language", "language": "language",
    "original_language": "language",
    "isbn": "isbn", "isbns": "isbn", "isbn13": "isbn", "isbn10": "isbn",
}
_COPY_ONLY = {"pages", "page_count", "number_of_pages", "publication_year",
              "dates", "published_at", "publication_date", "price",
              "series_index", "collection_index", "description", "summary", "synopsis"}


def canonical_book_isbn(value: object) -> str:
    """Canonical Bookland ISBN only (not arbitrary EAN or invalid checksum)."""
    text = str(value).strip().upper()
    # Formatting separators are allowed; never extract a coincidental ISBN
    # from an arbitrary supplier reference or a longer prose value.
    if not re.fullmatch(r"[0-9X\s-]+", text):
        return ""
    raw = re.sub(r"[\s-]", "", text)
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


# Commercial declarations remain separate from a purchased/local edition.
# No right, identifier or file-format key can cross this namespace. Values
# are copy-only so a commercial label cannot become a typed edition search.
_PUBLIC_PRODUCT_FIELDS = (
    ("brand", "Marque"),
    ("collection", "Collection déclarée"),
    ("publication_date", "Parution du produit"),
    ("pagination_label", "Pagination déclarée (support non précisé)"),
    ("language_label", "Langue déclarée"),
    ("selected_format_label", "Format commercial sélectionné"),
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
    if re.search(
        r"(?i)(?:\b(?:access_token|refresh_token|token|auth|authorization|cookie|"
        r"password|secret|signature)\s*[:=]|\bbasic\s+\S+|"
        r"[^\s<>@]+@[^\s<>@]+\.[^\s<>@]+)", result,
    ):
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
        if value and key in {"description", "summary", "synopsis"}:
            # Reuse the plain-text contract; never render provider markup.
            value = _safe_text(description_text(value))
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


# Age ranges are public bibliographic assertions only when their kind and
# numeric bounds are exact. Never promote free provider labels or inferred
# ranges as publisher-attested ages; keep each assertion separate.
_AGE_KINDS = {
    "publisher": "Âge éditeur",
    "reading": "Âge de lecture",
    "interest": "Âge d’intérêt",
    "inferred": "Âge inféré (non attesté)",
}


def _youth_age_items(raw: object):
    if not isinstance(raw, (tuple, list)):
        return
    for index, record in enumerate(raw):
        if index >= _MAX_COLLECTION_ENTRIES:
            yield "Âge", "Liste longue : tranches supplémentaires non affichées", "copy"
            break
        if not isinstance(record, Mapping):
            continue
        kind = record.get("age_kind")
        if type(kind) is not str or kind not in _AGE_KINDS:
            continue
        low, high = record.get("age_min"), record.get("age_max")
        if low is not None and (type(low) is not int or not 0 <= low < 18):
            continue
        if high is not None and (type(high) is not int or not 0 <= high <= 18):
            continue
        if (low is None and high is None) or (
            low is not None and high is not None and low > high
        ):
            continue
        if low is None:
            age = f"Jusqu’à {high} ans"
        elif high is None:
            age = f"À partir de {low} ans"
        elif low == high:
            age = f"{low} ans"
        else:
            age = f"De {low} à {high} ans"
        yield _AGE_KINDS[kind], age, "copy"


def detail_metadata_items(metadata: object) -> tuple[tuple[str, str, str], ...]:
    """Return explicit bibliographic facts only; no network, HTML or raw dict."""
    if not isinstance(metadata, Mapping):
        return ()
    rows: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    omitted = 0
    for prefix, data, public_product in (
        ("", metadata, False),
        ("Catalogue · ", metadata.get("catalogue_metadata"), False),
        ("EPUB · ", metadata.get("epub_metadata"), False),
        ("Fiche publique · ", metadata.get("public_product_metadata"), True),
    ):
        if not isinstance(data, Mapping):
            continue
        known = (
            (label, value, "copy" if public_product else _FIELD_KIND.get(
                key, "copy" if key in _COPY_ONLY else "all",
            ))
            for key, label in (_PUBLIC_PRODUCT_FIELDS if public_product else _FIELDS)
            for value in _values(data.get(key), key)
        )
        for label, value, kind in chain(
            known, () if public_product else _identifier_items(data.get("identifiers")),
            () if public_product else _youth_age_items(data.get("youth_age_ranges")),
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
