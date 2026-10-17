"""P0 #1072: explicit owned Numilog product details, no acquisition or live I/O.

The catalogue HTML is a sanitized capture, while all product Book JSON-LD
payloads in this module are SYNTHETIC. No fulfillment URL or account cookies.
"""
from dataclasses import replace
import json
from pathlib import Path

import pytest

from cifereca.plugin_api import ProviderCapabilityError, ProviderError
from cifereca_numilog import NumilogSession
from cifereca_numilog_enrichment import merge_attested_owned_public_detail
from cifereca_numilog_public import parse_public_search
from cifereca_numilog_public_detail import parse_public_detail_jsonld
import cifereca_numilog_public_client as client


FIX = Path(__file__).parent / "fixtures"
GOLDEN = json.loads((FIX / "public_search_jefferson_20261016.expected.json").read_text())
PAGE = parse_public_search((FIX / "public_search_jefferson_20261016.html").read_text(),
                           GOLDEN["source_url"])
URL = PAGE.items[0].friendly_url


def session(account="alice"):
    instance = NumilogSession(
        credential_id=account, label="Numilog", credentials={}, options={},
        config={}, diagnostics=None,
    )
    instance.connected = True
    return instance


def owned_row(url=URL):
    return {"lid": "42", "variant": "()", "title": PAGE.items[0].title,
            "cover": None, "public_url": url}


def install_rows(instance, records):
    calls = []
    def rows(**kwargs):
        calls.append(kwargs)
        assert not kwargs.get("include_delivery")  # Never parse signed URLs.
        return list(records)
    instance._rows = rows
    return calls


def detail(product_url=URL, name=None, **changes):
    record = {"@context": "https://schema.org", "@type": "Book",
              "url": product_url, "name": name or PAGE.items[0].title,
              "author": {"@type": "Person", "name": "Auteure de la fiche"},
              "publisher": {"name": "Éditeur de la fiche"},
              "description": "Description plus riche de la fiche publique",
              "inLanguage": "fr", "isbn": "9782075241939"}
    record.update(changes)
    return parse_public_detail_jsonld(
        '<script type="application/ld+json">' + json.dumps(record) + '</script>',
        product_url,
    )


def test_explicit_owned_detail_requires_fresh_purchase_and_exact_public_listing(monkeypatch):
    s = session()
    records = install_rows(s, [owned_row()])
    calls = []
    # Synthetic fixture card without author => demonstrate legitimate fill.
    card = replace(PAGE.items[0], author=None)
    monkeypatch.setattr(client, "fetch_public_search",
                        lambda query, *, page, field: calls.append(("search", query, page, field))
                        or replace(PAGE, items=(card,)))
    monkeypatch.setattr(client, "fetch_public_detail",
                        lambda url: calls.append(("detail", url)) or detail())
    try:
        stale = s._normalize(owned_row())
        stale.title = "UNCHECKED TITLE"
        stale.metadata["public_detail_enrichment"] = {"pretend": True}
        result = s.enrich_owned_public_detail(stale)
        assert records == [{"enrichment_record_id": s._id(owned_row())}]
        assert calls == [("search", PAGE.items[0].title, 0, "all"),
                         ("detail", URL)]
        assert result.title == PAGE.items[0].title
        assert result.author == "Auteure de la fiche"
        assert result.publisher == PAGE.items[0].publisher  # Listing wins.
        assert result.owned is True and result.downloadable is True
        assert result.source_credential == "alice" and result.source_collection == "library"
        assert result.download_url is None and result.format is None
        assert result.price is None and result.currency is None
        proof = result.metadata["public_detail_enrichment"]
        assert proof["status"] == "attested_public_product"
        assert proof["format_edition_proven"] is False
        assert proof["rights_proven_by_public_detail"] is False
        assert proof["isbn_edition_unspecified"] == "9782075241939"
        assert proof["language"] == "fr"
        assert "isbn_digital" not in result.metadata and "isbn_print" not in result.metadata
        assert proof["field_evidence"]["author"]["kind"] == "numilog.public_product_jsonld"
        assert "publisher" in proof["listing_conflicts"]
        assert stale.metadata["public_detail_enrichment"] == {"pretend": True}
    finally:
        s.close()


def test_no_owned_public_url_skips_all_anonymous_network(monkeypatch):
    s = session()
    install_rows(s, [owned_row(url=None)])
    monkeypatch.setattr(client, "fetch_public_search",
                        lambda *_a, **_kw: pytest.fail("search must not happen"))
    monkeypatch.setattr(client, "fetch_public_detail",
                        lambda *_a, **_kw: pytest.fail("detail must not happen"))
    try:
        result = s.enrich_owned_public_detail(s._normalize(owned_row()))
        assert result.friendly_url is None and result.owned
    finally:
        s.close()


def test_first_page_without_exact_edition_must_not_fetch_detail(monkeypatch):
    s = session()
    install_rows(s, [owned_row(url="https://www.numilog.com/123123/other.ebook")])
    monkeypatch.setattr(client, "fetch_public_search", lambda *_a, **_kw: PAGE)
    monkeypatch.setattr(client, "fetch_public_detail",
                        lambda *_a, **_kw: pytest.fail("unexpected product GET"))
    try:
        row = s.enrich_owned_public_detail(s._normalize(owned_row()))
        assert "public_detail_enrichment" not in row.metadata
        assert row.friendly_url.endswith("/123123/other.ebook")
    finally:
        s.close()


def test_revoked_purchase_fails_before_anonymous_network(monkeypatch):
    s = session()
    install_rows(s, [])
    monkeypatch.setattr(client, "fetch_public_search",
                        lambda *_a, **_kw: pytest.fail("unexpected public search"))
    monkeypatch.setattr(client, "fetch_public_detail",
                        lambda *_a, **_kw: pytest.fail("unexpected public detail"))
    try:
        with pytest.raises(ProviderError, match="non attestée"):
            s.enrich_owned_public_detail(s._normalize(owned_row()))
    finally:
        s.close()


def test_cross_account_fails_before_library_or_public_network(monkeypatch):
    alice, bob = session("alice"), session("bob")
    bob._rows = lambda **kwargs: pytest.fail("must not read Bob")
    monkeypatch.setattr(client, "fetch_public_search", lambda *_a, **_kw: pytest.fail("public search"))
    monkeypatch.setattr(client, "fetch_public_detail", lambda *_a, **_kw: pytest.fail("public detail"))
    try:
        with pytest.raises(ProviderError, match="autre connexion"):
            bob.enrich_owned_public_detail(alice._normalize(owned_row()))
    finally:
        alice.close()
        bob.close()


def test_public_detail_title_conflict_aborts_projection(monkeypatch):
    s = session()
    install_rows(s, [owned_row()])
    monkeypatch.setattr(client, "fetch_public_search", lambda *_a, **_kw: PAGE)
    monkeypatch.setattr(client, "fetch_public_detail",
                        lambda *_a, **_kw: detail(name="Another book entirely"))
    try:
        with pytest.raises(ProviderCapabilityError, match="public_detail_title_conflict"):
            s.enrich_owned_public_detail(s._normalize(owned_row()))
    finally:
        s.close()


def test_product_detail_http_refusal_has_no_private_retry_or_fallback(monkeypatch):
    s = session()
    rows = install_rows(s, [owned_row()])
    monkeypatch.setattr(client, "fetch_public_search", lambda *_a, **_kw: PAGE)
    monkeypatch.setattr(client, "fetch_public_detail",
                        lambda *_a, **_kw: (_ for _ in ()).throw(
                            ProviderError("Numilog : public_detail_rate_limited")))
    try:
        with pytest.raises(ProviderError, match="public_detail_rate_limited"):
            s.enrich_owned_public_detail(s._normalize(owned_row()))
        assert len(rows) == 1
    finally:
        s.close()


def test_forged_listing_and_jsonld_evidence_never_merge():
    s = session()
    try:
        fresh = s._normalize(owned_row())
        with pytest.raises(ProviderCapabilityError, match="evidence_conflict"):
            merge_attested_owned_public_detail(fresh, detail())
        forged = replace(fresh, metadata={
            **fresh.metadata, "public_enrichment": {
                "status": "attested_listing_match", "scope": "public_listing_only",
                "product_url": URL, "source_url": GOLDEN["source_url"],
                "field_evidence": {},
            },
        })
        bad = detail()
        bad["field_evidence"]["title"]["source_url"] = "https://evil.test/PRIVATE"
        with pytest.raises(ProviderCapabilityError, match="evidence_conflict"):
            merge_attested_owned_public_detail(forged, bad)
        with pytest.raises(ProviderCapabilityError, match="evidence_conflict"):
            merge_attested_owned_public_detail(forged, {**detail(), "owned": True})
    finally:
        s.close()


def test_ordinary_inventory_search_and_holdings_never_trigger_product_get(monkeypatch):
    s = session()
    install_rows(s, [owned_row()])
    monkeypatch.setattr(client, "fetch_public_search",
                        lambda *_a, **_kw: pytest.fail("no implicit public search"))
    monkeypatch.setattr(client, "fetch_public_detail",
                        lambda *_a, **_kw: pytest.fail("no implicit public detail"))
    try:
        assert len(s.library()) == 1
        assert len(s.search(PAGE.items[0].title)) == 1
        row = s._normalize(owned_row())
        assert s.holdings(row).availability_status == "owned"
    finally:
        s.close()
