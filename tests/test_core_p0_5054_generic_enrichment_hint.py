"""#5054: provider-neutral, non-authoritative explicit metadata action hints."""
from dataclasses import replace

import pytest

from cifereca.core.provider_explicit_enrichment import can_enrich_provider_detail_result
from cifereca.models import SearchResult


def _row(**changes):
    values = dict(
        id="fixture:42", record_id="fixture:42", docbase="SYNTHETIC",
        medium="digital", title="Fixture", author=None, type="ebook",
        format=None, publisher=None, published_at=None, description=None,
        thumbnail_url=None, friendly_url="https://fictional.invalid/books/42",
        scenario="SYNTHETIC", source_provider="provider-fictional",
        source_credential="alice", source_collection="library",
        owned=True, downloadable=True, download_url=None, metadata={},
    )
    values.update(changes)
    return SearchResult(**values)


@pytest.mark.parametrize("provider", ["provider-fictional", "future-store", "future-lender"])
def test_owned_visibility_not_tied_to_provider_identity(provider):
    assert can_enrich_provider_detail_result(_row(source_provider=provider))


def test_public_shape_is_not_a_private_entitlement():
    public = _row(source_provider="future-catalog", source_credential="__public__",
                  source_collection="public_catalog", owned=False, downloadable=False)
    assert can_enrich_provider_detail_result(public)
    assert not can_enrich_provider_detail_result(replace(public, owned=True))
    assert not can_enrich_provider_detail_result(replace(public, downloadable=True))


@pytest.mark.parametrize("changes", [
    {"source_credential": ""},
    {"source_credential": "__public__"},
    {"owned": False},
    {"source_collection": "public_catalog"},
    {"download_url": "https://fictional.invalid/private-token"},
    {"friendly_url": "http://fictional.invalid/books/42"},
    {"friendly_url": "https://user:pass@fictional.invalid/books/42"},
    {"friendly_url": "https://fictional.invalid/books/42?token=secret"},
    {"friendly_url": "https://fictional.invalid/"},
])
def test_incomplete_or_ambiguous_hint_is_not_advertised(changes):
    assert not can_enrich_provider_detail_result(_row(**changes))


def test_existing_optional_exact_provider_filter_is_only_a_hint():
    row = _row()
    assert can_enrich_provider_detail_result(
        row, private_provider_id="provider-fictional", public_provider_id="future-catalog",
    )
    assert not can_enrich_provider_detail_result(
        row, private_provider_id="unrelated", public_provider_id="future-catalog",
    )
