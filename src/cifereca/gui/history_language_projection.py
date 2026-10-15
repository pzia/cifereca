"""Shared, headless language projection for catalog-backed list filters (#1129).

Matching remains provider+item scoped. A history filter can normalize one
catalog snapshot instead of querying the entire catalog once per history
row. No provider fetch, new cache lifetime or persistent user state.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping

from ..language import normalize_language_tag


def metadata_languages(metadata: Mapping | None) -> set[str]:
    """Keep the original singular/plural precedence and supported sequence types."""
    meta = dict(metadata or {})
    raw = meta.get("languages") or meta.get("language") or ()
    values = raw if isinstance(raw, (list, tuple, set)) else (raw,)
    return {
        tag for value in values
        if (tag := normalize_language_tag(value))
    }


def item_languages(
    provider_id: str,
    item_id: str | None,
    metadata: Mapping | None = None,
    *,
    catalog_rows: Iterable[Mapping] = (),
) -> set[str]:
    """Union direct metadata with matching catalog rows, never other providers."""
    languages = metadata_languages(metadata)
    if item_id:
        provider_key = str(provider_id or "")
        item_key = str(item_id)
        for row in catalog_rows:
            if str(row.get("provider_id") or "") != provider_key:
                continue
            if str(row.get("item_id") or "") != item_key:
                continue
            languages.update(metadata_languages(row.get("metadata")))
    return languages


def history_rows_with_languages(rows, wanted_languages, catalog_rows):
    """Filter an ordered history batch using a single local catalog snapshot.

    The lookup is built once for the whole batch. Entries with the same book
    identifier from different providers must never contribute to each other's
    language facets. No fallback language is inferred from title/author.
    """
    wanted = set(wanted_languages)
    if not wanted:
        return list(rows)

    keys = {
        (str(item.source_provider or ""), str(item.record_id or item.id))
        for item in rows if (item.record_id or item.id)
    }
    matched: dict[tuple[str, str], set[str]] = {}
    for row in catalog_rows:
        key = (
            str(row.get("provider_id") or ""),
            str(row.get("item_id") or ""),
        )
        if key in keys:
            matched.setdefault(key, set()).update(
                metadata_languages(row.get("metadata"))
            )

    return [
        item for item in rows
        if (item.record_id or item.id)
        and matched.get(
            (str(item.source_provider or ""), str(item.record_id or item.id)),
            set(),
        ).intersection(wanted)
    ]
