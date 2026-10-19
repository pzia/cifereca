"""Explicit public Book detail projection and identity isolation (#1886, #1072)."""
from dataclasses import replace
import json
from pathlib import Path

import pytest

from cifereca.plugin_api import ProviderCapabilityError, ProviderError
import cifereca_numilog_public_client as client
import cifereca_numilog_public_provider as public
from cifereca_numilog_public import parse_public_search
from cifereca_numilog_public_detail import merge_public_detail, parse_public_detail_jsonld

FIXTURES = Path(__file__).with_name("fixtures")
GOLDEN = json.loads((FIXTURES / "public_search_jefferson_20261016.expected.json").read_text())
HTML = (FIXTURES / "public_search_jefferson_20261016.html").read_text()


def catalogue_item():
    card = parse_public_search(HTML, GOLDEN["source_url"]).items[0]
    return replace(card, source_provider="numilog-catalog")


def book_detail(item, **overrides):
    book = {
        "@context": "https://schema.org",
        "@type": "Book",
        "url": item.friendly_url,
        "name": item.title,
        "description": "Résumé de la fiche publique.",
        "author": [{"@type": "Person", "name": "Autrice fictive"}],
        "publisher": {"name": "Éditeur public"},
        "isbn": "9782075241939",
    }
    book.update(overrides)
    html = '<script type="application/ld+json">' + json.dumps(book) + '</script>'
    return parse_public_detail_jsonld(html, item.friendly_url)


def public_session():
    session = public.PLUGIN.create_session(
        credential_id="__public__", label="Catalogue",
        credentials={}, options={}, config={}, storage=None, diagnostics=None,
    )
    session.connect()
    return session


def test_projection_adds_proven_details_without_assuming_purchase():
    original = catalogue_item()
    missing = replace(original, author=None, description=None, publisher=None)
    detail = book_detail(missing)
    enriched = merge_public_detail(missing, detail)
    assert enriched is not missing
    assert enriched.author == "Autrice fictive"
    assert enriched.description == "Résumé de la fiche publique."
    assert enriched.publisher == "Éditeur public"
    assert enriched.owned is False and enriched.downloadable is False
    assert enriched.download_url is None
    assert enriched.record_id == missing.record_id
    assert enriched.price == missing.price
    assert enriched.metadata["price"] == missing.metadata["price"]
    assert "public_detail" not in missing.metadata
    assert enriched.metadata["public_detail"]["fields"]["isbn_edition_unspecified"] == "9782075241939"
    assert "isbn_digital" not in enriched.metadata["public_detail"]["fields"]
    assert enriched.metadata["public_detail"]["rights_proven"] is False


def test_conflicts_are_exposed_without_overwriting_listing_values():
    item = catalogue_item()
    detail = book_detail(item, description="Different",
                         publisher={"name": "Autre éditeur"}, author="Autre auteur")
    merged = merge_public_detail(item, detail)
    assert merged.title == item.title and merged.author == item.author
    assert merged.publisher == item.publisher and merged.description == item.description
    assert {"authors", "publisher", "description"}.issubset(
        set(merged.metadata["public_detail"]["listing_conflicts"])
    )
    assert "title" not in merged.metadata["public_detail"]["listing_conflicts"]


@pytest.mark.parametrize("changes", [
    {"source_provider": "numilog"},
    {"source_credential": "private"},
    {"source_collection": "personal_library"},
    {"owned": True},
    {"downloadable": True},
    {"download_url": "https://example.test/private?token=PRIVATE_MARKER"},
    {"record_id": "numilog:public:other"},
    {"friendly_url": "https://example.test/1958476/other.ebook"},
])
def test_foreign_or_owned_result_rejected_before_http(monkeypatch, changes):
    item = replace(catalogue_item(), **changes)
    monkeypatch.setattr(client, "fetch_public_detail", lambda *_a, **_kw: pytest.fail("unexpected HTTP"))
    session = public_session()
    try:
        with pytest.raises(ProviderCapabilityError, match="detail_evidence_conflict"):
            session.enrich_public_detail(item)
    finally:
        session.close()


def test_public_session_is_explicit_and_does_not_fetch_during_connect(monkeypatch):
    fetched = []
    monkeypatch.setattr(client, "fetch_public_detail",
                        lambda url: fetched.append(url) or book_detail(catalogue_item()))
    session = public_session()
    item = catalogue_item()
    assert fetched == []
    try:
        merged = session.enrich_public_detail(item)
        assert fetched == [item.friendly_url]
        assert merged.record_id == item.record_id
        assert not merged.owned and not merged.downloadable
    finally:
        session.close()
    with pytest.raises(ProviderError, match="non connecté"):
        session.enrich_public_detail(item)
    assert fetched == [item.friendly_url]


@pytest.mark.parametrize("modification", [
    {"product_url": "https://www.numilog.com/1958477/other.ebook"},
    {"owned": True},
    {"downloadable": True},
    {"download_url": "https://example.test/file"},
])
def test_mismatching_detail_data_is_never_merged(modification):
    item = catalogue_item()
    detail = {**book_detail(item), **modification}
    with pytest.raises(ProviderCapabilityError, match="detail_evidence_conflict"):
        merge_public_detail(item, detail)


def test_spoofed_field_provenance_cannot_be_published():
    item = catalogue_item()
    detail = book_detail(item)
    detail["field_evidence"]["title"]["edition_proven"] = True
    with pytest.raises(ProviderCapabilityError, match="detail_evidence_conflict"):
        merge_public_detail(item, detail)


def test_mismatched_title_does_not_fill_missing_details():
    item = replace(catalogue_item(), author=None, publisher=None, description=None)
    detail = book_detail(item, name="Different book")
    with pytest.raises(ProviderCapabilityError, match="detail_title_conflict"):
        merge_public_detail(item, detail)
    assert item.author is None and item.publisher is None
    assert item.description is None and item.title == catalogue_item().title
    assert "public_detail" not in item.metadata
    assert item.owned is False
