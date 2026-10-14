"""Read-only language evidence used by Loans and History (#1129).

This module has no GTK dependency and introduces no new provider requests.
Explicit metadata and the matching *local catalog* rows are the only
language sources. An unknown value is never inferred into a language.
"""
from __future__ import annotations

from typing import Any

from cifereca.language import normalize_language_tag


def known_item_languages(
    window,
    provider_id: str,
    item_id: str | None,
    metadata: dict[str, Any] | None = None,
) -> set[str]:
    values = []
    meta = dict(metadata or {})
    raw = meta.get("languages") or meta.get("language") or ()
    values.extend(raw if isinstance(raw, (list, tuple, set)) else (raw,))
    if item_id:
        for row in window.service.storage.catalog_entries():
            if str(row.get("provider_id") or "") != str(provider_id or ""):
                continue
            if str(row.get("item_id") or "") != str(item_id):
                continue
            row_meta = dict(row.get("metadata") or {})
            raw = row_meta.get("languages") or row_meta.get("language") or ()
            values.extend(raw if isinstance(raw, (list, tuple, set)) else (raw,))
    return {tag for value in values if (tag := normalize_language_tag(value))}
