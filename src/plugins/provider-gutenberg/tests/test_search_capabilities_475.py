from __future__ import annotations

from cifereca.plugin_api import ProviderSearchCapabilities
from cifereca_gutenberg import (
    DESCRIPTOR,
    GUTENBERG_PAGE_SIZE,
    MAX_REQUESTED_PAGES,
)


def test_declared_search_capabilities_match_gutenberg_behavior():
    caps = DESCRIPTOR.search_capabilities

    assert isinstance(caps, ProviderSearchCapabilities)
    assert caps.fields == ("all", "title", "author", "subject", "language")
    assert caps.scopes == ("all", "digital")
    assert caps.pagination == "next-link"
    assert caps.page_size == GUTENBERG_PAGE_SIZE == 25
    assert caps.max_pages == MAX_REQUESTED_PAGES == 101


def test_unsupported_ui_fields_are_not_advertised():
    caps = DESCRIPTOR.search_capabilities

    for field in ("isbn", "series", "collection"):
        assert caps.supports_field(field) is False


def test_locally_filtered_fields_are_documented_as_non_server_fields():
    notes = " ".join(DESCRIPTOR.search_capabilities.notes).casefold()

    assert "local post-filters" in notes
    assert "not server-side fields" in notes
    assert "bookshelf" in notes
