"""Paris may advertise a borrow-action UI hint; Core alone authorizes POST.

Synthetic SearchResult records, no HTTP, login or real account.
"""
from dataclasses import replace

import pytest

from cifereca.plugin_api import SearchResult
from cifereca_paris import _project_paris_acquisition_formats


def book(*, docbase="DILICOM", medium="digital", provider="paris", record_id="edition-a"):
    return SearchResult(
        id="synthetic-a", record_id=record_id, docbase=docbase,
        medium=medium, title="Synthetic book", author=None, type="ebook",
        format="EPUB", publisher=None, published_at=None,
        description=None, thumbnail_url=None, friendly_url=None,
        scenario="EBOOKS", source_provider=provider,
        source_credential="card-a", source_collection="catalog",
        metadata={},
    )


def test_paris_digital_catalogue_emits_exact_read_only_presentation_hint():
    row = book()
    projected = _project_paris_acquisition_formats(row)
    assert projected.metadata["manual_borrow_presentation"] == {
        "provider_id": "paris", "docbase": "DILICOM", "record_id": "edition-a",
    }
    assert "admission" not in projected.metadata
    assert "loan_claim" not in projected.metadata
    assert "borrow_authorized" not in projected.metadata


@pytest.mark.parametrize(("docbase", "medium", "provider", "record_id"), [
    ("SYRACUSE", "digital", "paris", "edition-a"),
    ("DILICOM", "physical", "paris", "edition-a"),
    ("DILICOM", "digital", "other", "edition-a"),
    ("DILICOM", "digital", "paris", ""),
])
def test_other_scope_never_advertises_paris_manual_borrow(
    docbase, medium, provider, record_id,
):
    row = book(docbase=docbase, medium=medium, provider=provider,
               record_id=record_id)
    # Invalid stale metadata cannot survive a new projection.
    row.metadata["manual_borrow_presentation"] = {
        "provider_id": "paris", "docbase": "DILICOM", "record_id": "stale",
    }
    projected = _project_paris_acquisition_formats(row)
    assert "manual_borrow_presentation" not in projected.metadata
