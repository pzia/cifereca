"""#5056: catalogue-only rows must not dispatch private holdings by brand."""
from dataclasses import replace

import pytest

from cifereca.models import SearchResult
from cifereca.services import LibraryService


def row(*, provider="library.future", credential="foreign", collection="catalog",
        metadata=None):
    return SearchResult(
        id="edition", record_id="edition", docbase="CATALOG",
        medium="digital", title="Example", author=None, type=None,
        format=None, publisher=None, published_at=None,
        description=None, thumbnail_url=None, friendly_url=None,
        scenario="CATALOG", source_provider=provider,
        source_credential=credential, source_collection=collection,
        metadata={} if metadata is None else metadata,
    )


@pytest.mark.parametrize("item", [
    row(provider="library.future",
        metadata={"catalogue_access": "shared_read_only"}),
    row(provider="kobo", metadata={"catalogue_access": "shared_read_only"}),
    row(provider="numilog", credential="__public__",
        collection="public_catalog"),
    row(provider="library.future", credential="__public__",
        collection="public_catalog"),
])
def test_read_only_catalogue_never_calls_borrower_holdings(item):
    # A bare object intentionally has no session, cache, or provider methods.
    # The projection must return before any private broker access.
    detail = LibraryService.holdings(object(), item, force=True)
    assert detail.holdings == []
    assert detail.availability_status == "unknown"
    assert detail.source_provider == item.source_provider
    assert detail.source_credential == item.source_credential


def test_unbound_private_source_must_not_query_borrower_holdings():
    # An unmarked authenticated row cannot enter a private provider session
    # without an exact owner binding, even if the service is unavailable.
    detail = LibraryService.holdings(object(), row(metadata={}), force=True)
    assert detail.holdings == []
    assert detail.availability_status == "unknown"
