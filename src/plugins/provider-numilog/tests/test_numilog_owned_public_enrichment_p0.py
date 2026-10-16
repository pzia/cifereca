"""P0 #1072: edition-specific Numilog owned-book listing enrichment.

All public HTML is the sanitized Jefferson fixture. No real account, signed
URL, ACSM, login, purchase, product-detail scraping or network I/O occurs.
"""
from dataclasses import replace
from pathlib import Path

import pytest

from cifereca.plugin_api import ProviderError, ProviderCapabilityError
from cifereca_numilog import NumilogSession
from cifereca_numilog_html import LIBRARY
from cifereca_numilog_enrichment import merge_attested_public_listing
from cifereca_numilog_public import parse_public_search
import cifereca_numilog_public_client as public_client


FIXTURE = (Path(__file__).parent / "fixtures" /
           "public_search_jefferson_20261016.html").read_text()
SOURCE = ("https://www.numilog.com/Pages/Recherche/ResultatRecherche.aspx"
          "?mode=brSimple&query=jefferson")
PAGE = parse_public_search(FIXTURE, SOURCE)
PROOF = PAGE.items[0].friendly_url


def make_session(account):
    session = NumilogSession(
        credential_id=account, label="Numilog", credentials={},
        options={}, config={}, diagnostics=None,
    )
    session.connected = True
    return session


def owned_row(*, url=PROOF, lid="42", title="Jefferson prend des risques"):
    return {
        "lid": lid, "variant": "()",
        "title": title, "cover": None, "href": LIBRARY,
        "public_url": url,
    }


def install_inventory(session, rows):
    calls = []

    def query_rows(**kw):
        calls.append(kw)
        assert set(kw) <= {"enrichment_record_id"}  # Never include_delivery=True.
        return list(rows)

    session._rows = query_rows
    return calls


def test_one_explicit_call_enriches_exact_owned_edition_without_entitlement_changes(monkeypatch):
    session = make_session("alice")
    row = owned_row()
    inventory = install_inventory(session, [row])
    visits = []
    monkeypatch.setattr(
        public_client, "fetch_public_search",
        lambda query, *, page, field: visits.append((query, page, field)) or PAGE,
    )
    try:
        request = session._normalize(row)
        # Untrusted cached caller state is never a source of rights or fields.
        request.title = "UNTRUSTED STALE TITLE"
        request.price = "0.01"
        request.metadata["public_enrichment"] = {"pretend": "owned"}
        result = session.enrich_owned_public_listing(request)
        assert visits == [("Jefferson prend des risques", 0, "all")]
        assert inventory == [{"enrichment_record_id": session._id(row)}]
        assert result.record_id == request.record_id
        assert result.source_provider == "numilog"
        assert result.source_credential == "alice"
        assert result.source_collection == "library"
        assert result.owned is True and result.downloadable is True
        assert result.download_url is None
        assert result.title == row["title"]
        assert result.author == "Jean-Claude Mourlevat"
        assert result.publisher == "Gallimard Jeunesse"
        assert result.description and result.published_at == "2026-10-08"
        assert result.thumbnail_url
        assert result.metadata["public_enrichment"]["status"] == "attested_listing_match"
        assert result.metadata["public_enrichment"]["product_url"] == PROOF
        assert result.metadata["public_enrichment"]["scope"] == "public_listing_only"
        assert result.metadata["public_enrichment"]["field_evidence"]["author"]["kind"] == "numilog.public_search_listing"
        assert result.metadata["public_offer_price"]["amount"] == "9.99"
        assert result.metadata["public_offer_price"]["currency"] == "EUR"
        assert result.price is None and result.currency is None  # Not an old purchase price.
        assert result.format is None and result.metadata["format_unverified"] is True
        assert "isbn_digital" not in result.metadata and "isbn_print" not in result.metadata
        assert "drm_label" not in result.metadata
        assert request.metadata["public_enrichment"] == {"pretend": "owned"}
    finally:
        session.close()


def test_no_public_url_means_no_anonymous_request(monkeypatch):
    session = make_session("alice")
    row = owned_row(url=None)
    inventory = install_inventory(session, [row])
    monkeypatch.setattr(
        public_client, "fetch_public_search",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("network")),
    )
    try:
        result = session.enrich_owned_public_listing(session._normalize(row))
        assert result.friendly_url is None
        assert result.author is None and result.price is None
        assert inventory == [{"enrichment_record_id": session._id(row)}]
    finally:
        session.close()


def test_homonymous_pdf_epub_are_not_fused_and_no_title_matching(monkeypatch):
    session = make_session("alice")
    pdf, epub = PAGE.items[1:3]
    assert pdf.title == epub.title and pdf.friendly_url != epub.friendly_url
    owned = owned_row(url=epub.friendly_url, title=epub.title)
    install_inventory(session, [owned])
    monkeypatch.setattr(public_client, "fetch_public_search", lambda *a, **kw: PAGE)
    try:
        result = session.enrich_owned_public_listing(session._normalize(owned))
        assert result.friendly_url == epub.friendly_url
        assert result.metadata["public_enrichment"]["product_url"] == epub.friendly_url
        assert result.owned is True
        assert result.format is None  # Even one listing card is not purchased-format proof.
        assert "isbn_digital" not in result.metadata
    finally:
        session.close()


def test_edition_absent_from_first_search_page_is_not_inferred(monkeypatch):
    session = make_session("alice")
    row = owned_row(url="https://www.numilog.com/123123/no-page-match.ebook")
    install_inventory(session, [row])
    monkeypatch.setattr(public_client, "fetch_public_search", lambda *a, **kw: PAGE)
    try:
        result = session.enrich_owned_public_listing(session._normalize(row))
        assert result.author is None and result.publisher is None
        assert "public_offer_price" not in result.metadata
        assert result.owned is True  # Only independently attested by owned inventory.
    finally:
        session.close()


def test_source_account_is_checked_before_private_and_public_request(monkeypatch):
    alice = make_session("alice")
    bob = make_session("bob")
    rows_called = []
    bob._rows = lambda **kw: rows_called.append(True) or [owned_row()]
    monkeypatch.setattr(
        public_client, "fetch_public_search",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("public call")),
    )
    try:
        foreign = alice._normalize(owned_row())
        with pytest.raises(ProviderError, match="autre connexion"):
            bob.enrich_owned_public_listing(foreign)
        assert rows_called == []
        assert foreign.source_credential == "alice"
    finally:
        alice.close()
        bob.close()


def test_revoked_or_missing_purchase_rejects_before_public_request(monkeypatch):
    session = make_session("alice")
    install_inventory(session, [])
    monkeypatch.setattr(
        public_client, "fetch_public_search",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("public call")),
    )
    try:
        with pytest.raises(ProviderError, match="non attestée"):
            session.enrich_owned_public_listing(session._normalize(owned_row()))
    finally:
        session.close()


def test_duplicate_attested_public_url_is_rejected_not_picked():
    owned = make_session("alice")
    try:
        row = owned._normalize(owned_row())
        with pytest.raises(ProviderCapabilityError, match="ambiguous"):
            merge_attested_public_listing(row, (PAGE.items[0], PAGE.items[0]))
    finally:
        owned.close()


def test_contradictory_or_unscoped_public_evidence_is_rejected():
    session = make_session("alice")
    try:
        target = session._normalize(owned_row())
        fake = replace(PAGE.items[0], metadata={
            **PAGE.items[0].metadata, "source_item_id": "999999",
        })
        with pytest.raises(ProviderCapabilityError, match="evidence_conflict"):
            merge_attested_public_listing(target, (fake,))
        forged = replace(PAGE.items[0], owned=True)
        with pytest.raises(ProviderCapabilityError, match="evidence_conflict"):
            merge_attested_public_listing(target, (forged,))
        contaminated = replace(PAGE.items[0], metadata={
            **PAGE.items[0].metadata, "source_url": SOURCE + "&auth=PRIVATE",
        })
        with pytest.raises(ProviderCapabilityError, match="evidence_conflict"):
            merge_attested_public_listing(target, (contaminated,))
    finally:
        session.close()


def test_search_inventory_and_detail_paths_do_not_enrich_implicitly(monkeypatch):
    session = make_session("alice")
    row = owned_row()
    inventory = install_inventory(session, [row])
    monkeypatch.setattr(
        public_client, "fetch_public_search",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("surprise GET")),
    )
    try:
        assert len(session.library()) == 1
        assert len(session.search("Jefferson")) == 1
        session.holdings(session._normalize(row))
        assert inventory == [{}, {}]
    finally:
        session.close()


def test_public_error_propagates_without_silent_private_fallback(monkeypatch):
    session = make_session("alice")
    row = owned_row()
    inventory = install_inventory(session, [row])
    def offline(*args, **kwargs):
        raise ProviderError("Numilog : public_catalog_forbidden")
    monkeypatch.setattr(public_client, "fetch_public_search", offline)
    try:
        with pytest.raises(ProviderError, match="public_catalog_forbidden"):
            session.enrich_owned_public_listing(session._normalize(row))
        assert inventory == [{"enrichment_record_id": session._id(row)}]
    finally:
        session.close()
