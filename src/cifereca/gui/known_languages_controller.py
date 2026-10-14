"""Read-only language evidence for Loans and History (#1129).

The shared history_language_projection owns normalisation and scoped matching.
Single-book reads use one local catalogue snapshot only when item_id is set;
batched History filtering stays in its existing one-pass controller.
"""
from __future__ import annotations

from . import history_language_projection


def known_item_languages(window, provider_id: str, item_id: str | None, metadata: dict | None = None) -> set[str]:
    catalog_rows = window.service.storage.catalog_entries() if item_id else ()
    return history_language_projection.item_languages(
        provider_id, item_id, metadata, catalog_rows=catalog_rows,
    )
