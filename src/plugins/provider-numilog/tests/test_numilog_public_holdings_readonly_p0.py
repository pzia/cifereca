"""#1886/#1072: Core detail for Numilog public listings is cache-only.

The sanitized public search fixture is read offline. Selecting an item must
not open a product page, use cookies, trigger acquisition, or prove ownership.
"""
from dataclasses import replace
import json
from pathlib import Path

import pytest

from cifereca.plugin_api import ProviderCapabilityError, ProviderError
from cifereca_numilog_public import parse_public_search
import cifereca_numilog_public_client as client
import cifereca_numilog_public_provider as public

FIXTURES = Path(__file__).with_name("fixtures")
CAPTURE = (FIXTURES / "public_search_jefferson_20261016.html").read_text()
GOLDEN = json.loads((FIXTURES / "public_search_jefferson_20261016.expected.json").read_text())
PAGE = parse_public_search(CAPTURE, GOLDEN["source_url"])


def session():
    result = public.PLUGIN.create_session(
        credential_id="__public__", label="Catalogue Numilog",
        credentials={}, options={}, config={}, storage=None, diagnostics=None,
    )
    result.connect()
    return result


def public_item():
    row = PAGE.items[0]
    return replace(row, source_provider="numilog-catalog")


def test_core_detail_contract_is_read_only_and_available():
    assert public.DESCRIPTOR.capabilities.holdings is True
    assert public.DESCRIPTOR.capabilities.personal_library is False
    assert public.DESCRIPTOR.capabilities.direct_download is False
    s = session()
    try:
        row = public_item()
        d = s.holdings(row, search_terms="jefferson")
        assert d.record_id == row.record_id and d.docbase == row.docbase
        assert d.title == row.title and d.author == row.author
        assert d.year == row.published_at and d.thumbnail_url == row.thumbnail_url
        assert d.source_provider == "numilog-catalog"
        assert d.source_credential == "__public__"
        assert d.availability_status == "unknown"
        assert "achat non attesté" in d.availability_label
        assert d.holdings == []
        assert row.owned is False and row.downloadable is False and row.download_url is None
        assert s.acquisition_offers(row) == []
        assert row.metadata.get("public_detail") is None
    finally:
        s.close()


def test_selected_result_never_starts_public_or_private_http(monkeypatch):
    import httpx
    calls = []
    def forbidden(*_args, **_kwargs):
        calls.append(True)
        raise AssertionError("GET/POST during holdings")
    monkeypatch.setattr(httpx, "Client", forbidden)
    monkeypatch.setattr(client.HTTPX_TRANSPORT, "client", forbidden)
    monkeypatch.setattr(client, "fetch_public_search", forbidden)
    monkeypatch.setattr(client, "fetch_public_detail", forbidden)
    s = session()
    try:
        assert s.holdings(public_item()).availability_status == "unknown"
        assert calls == []
    finally:
        s.close()


@pytest.mark.parametrize("changes", [
    {"source_provider": "numilog"},
    {"source_credential": "private"},
    {"source_collection": "library"},
    {"owned": True},
    {"downloadable": True},
    {"download_url": "https://evil.example/PRIVATE_TOKEN"},
    {"record_id": "numilog:public:wrong"},
    {"friendly_url": "https://evil.example/1958476/fake.ebook"},
    {"scenario": "NUMILOG_LIBRARY"},
    {"docbase": "OTHER"},
    {"title": ""},
])
def test_invalid_public_source_is_rejected_without_access(monkeypatch, changes):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("unexpected network")
    monkeypatch.setattr(client, "fetch_public_detail", forbidden)
    s = session()
    try:
        with pytest.raises(ProviderCapabilityError, match="detail_evidence_conflict"):
            s.holdings(replace(public_item(), **changes))
    finally:
        s.close()


def test_refuses_offline_holdings_when_disconnected():
    s = session()
    s.close()
    with pytest.raises(ProviderError, match="non connecté"):
        s.holdings(public_item())


def test_synthetic_enrichment_fields_remain_bibliographic_not_entitlement():
    s = session()
    try:
        from cifereca_numilog_public_detail import merge_public_detail, parse_public_detail_jsonld
        row = public_item()
        book = {"@context": "https://schema.org", "@type": "Book",
                "url": row.friendly_url, "name": row.title,
                "inLanguage": "fr", "isbn": "9782075241939"}
        html = '<script type="application/ld+json">' + json.dumps(book) + '</script>'
        detail = parse_public_detail_jsonld(html, row.friendly_url)
        enriched = merge_public_detail(row, detail)
        d = s.holdings(enriched)
        assert d.availability_status == "unknown"
        assert d.holdings == []
        assert enriched.owned is False and enriched.downloadable is False
        assert enriched.download_url is None
        assert enriched.metadata["public_detail"]["fields"]["isbn_edition_unspecified"] == "9782075241939"
    finally:
        s.close()
