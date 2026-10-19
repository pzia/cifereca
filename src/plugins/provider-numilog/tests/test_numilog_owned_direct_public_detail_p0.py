"""P0 #1072: explicit authenticated title link can qualify public detail directly.

Fixtures and private accounts are wholly synthetic. No real session, HTTP,
cookies, ACSM, payment, local artifacts or real purchased-book evidence.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import json

import pytest

from cifereca.plugin_api import ProviderCapabilityError, ProviderError
from cifereca_numilog import NumilogSession
from cifereca_numilog_public import parse_public_search
from cifereca_numilog_public_detail import parse_public_detail_jsonld
from cifereca_numilog_enrichment import (
    merge_attested_owned_direct_public_detail, merge_attested_owned_public_detail,
)
import cifereca_numilog_public_client as public_client

ROOT = Path(__file__).parent / "fixtures"
URL = "https://www.numilog.com/Pages/Recherche/ResultatRecherche.aspx?mode=brSimple&query=jefferson"
PAGE = parse_public_search((ROOT / "public_search_jefferson_20261016.html").read_text(), URL)
PRIVATE = "VERY_PRIVATE_ADOBE_SESSION"


def session(account="alice"):
    s = NumilogSession(credential_id=account, label="Compte", credentials={},
                       options={}, config={}, diagnostics=None)
    s.connected = True
    return s


def card(index, *, lid="42", url=None, title=None):
    listed = PAGE.items[index]
    return {
        "lid": lid, "variant": "()", "title": listed.title if title is None else title,
        "cover": None, "public_url": listed.friendly_url if url is None else url,
    }


def private_rows(s, rows):
    calls = []
    def lookup(**kwargs):
        calls.append(kwargs)
        assert set(kwargs) == {"enrichment_record_id"}
        return list(rows)
    s._rows = lookup
    return calls


def public_detail(owned, *, title=None):
    node = {
        "@context": "https://schema.org", "@type": "Book",
        "name": owned.title if title is None else title,
        "url": owned.friendly_url, "author": [{"name": "Auteur attesté public"}],
        "publisher": {"name": "Éditeur attesté public"},
        "description": "Description commerciale complète, non liée aux droits.",
        "inLanguage": "fr", "isbn": "9782075241939",
    }
    html = '<script type="application/ld+json">' + json.dumps(node) + "</script>"
    return parse_public_detail_jsonld(html, owned.friendly_url)


def safe_assert(result, before, expected_url):
    assert result.record_id == before.record_id
    assert result.source_credential == before.source_credential
    assert result.source_provider == "numilog"
    assert result.source_collection == "library"
    assert result.owned is True and result.downloadable is True
    assert result.download_url is None
    assert result.format is None and result.metadata["format_unverified"] is True
    assert result.price is None and result.currency is None
    assert result.friendly_url == expected_url
    assert "isbn_digital" not in result.metadata
    assert "isbn_print" not in result.metadata
    assoc = result.metadata["public_enrichment"]
    assert assoc == {
        "status": "attested_owned_title_link",
        "scope": "owned_library_title_link_only",
        "product_url": expected_url,
        "field_evidence": {},
    }
    proof = result.metadata["public_detail_enrichment"]
    assert proof["association_evidence"] == "numilog.library_title_link"
    assert proof["association_scope"] == "authenticated_owned_book_product"
    assert proof["format_edition_proven"] is False
    assert proof["rights_proven_by_public_detail"] is False
    assert proof["public_jsonld"]["edition_proven"] is False
    assert proof["public_jsonld"]["rights_proven"] is False
    assert proof["public_jsonld"]["fields"]["isbn_edition_unspecified"] == "9782075241939"


@pytest.mark.parametrize("index,lid", [(0, "42"), (1, "43")])
def test_direct_owned_link_enriches_even_if_catalogue_first_page_is_missing(monkeypatch, index, lid):
    s = session()
    row = card(index, lid=lid)
    calls = private_rows(s, [row])
    urls = []
    monkeypatch.setattr(public_client, "fetch_public_search",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("NO SEARCH GET")))
    def fetch_product(url):
        urls.append(url)
        assert url == row["public_url"]
        return public_detail(s._normalize(row))
    monkeypatch.setattr(public_client, "fetch_public_detail", fetch_product)
    try:
        request = s._normalize(row)
        request.title = PRIVATE  # Do not let cached caller title become proof.
        request.metadata["public_enrichment"] = {"forged": PRIVATE}
        result = s.enrich_owned_direct_public_detail(request)
        safe_assert(result, s._normalize(row), row["public_url"])
        assert result.title == row["title"]
        assert result.description.startswith("Description commerciale complète")
        assert result.author == "Auteur attesté public"
        assert result.publisher == "Éditeur attesté public"
        assert result.metadata["public_detail_enrichment"]["public_jsonld"]["fields"]["language"] == "fr"
        assert calls == [{"enrichment_record_id": s._id(row)}]
        assert urls == [row["public_url"]]
        assert PRIVATE not in repr(result.metadata)
        assert request.metadata["public_enrichment"] == {"forged": PRIVATE}
    finally:
        s.close()


def test_homonymous_products_are_never_merged_into_a_purchased_format(monkeypatch):
    s = session()
    pdf = card(1, lid="42")
    epub = card(2, lid="43")
    assert pdf["title"] == epub["title"] and pdf["public_url"] != epub["public_url"]
    private_rows(s, [pdf, epub])
    observed = []
    def fetch(url):
        original = pdf if url == pdf["public_url"] else epub
        observed.append(url)
        return public_detail(s._normalize(original))
    monkeypatch.setattr(public_client, "fetch_public_search",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("SEARCH")))
    monkeypatch.setattr(public_client, "fetch_public_detail", fetch)
    try:
        a = s.enrich_owned_direct_public_detail(s._normalize(pdf))
        b = s.enrich_owned_direct_public_detail(s._normalize(epub))
        assert a.record_id != b.record_id
        assert a.friendly_url != b.friendly_url
        assert set(observed) == {a.friendly_url, b.friendly_url}
        safe_assert(a, s._normalize(pdf), pdf["public_url"])
        safe_assert(b, s._normalize(epub), epub["public_url"])
    finally:
        s.close()


def test_missing_title_link_returns_private_book_without_any_public_request(monkeypatch):
    s = session()
    row = card(0)
    row["public_url"] = None
    private_rows(s, [row])
    monkeypatch.setattr(public_client, "fetch_public_detail",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("NO PRODUCT GET")))
    try:
        result = s.enrich_owned_direct_public_detail(s._normalize(row))
        assert result.friendly_url is None
        assert "public_enrichment" not in result.metadata
        assert result.owned is True
    finally:
        s.close()


@pytest.mark.parametrize("row_type", ["revoked", "duplicate"])
def test_revoked_or_ambiguous_private_owner_blocks_public_read(monkeypatch, row_type):
    s = session()
    owned = card(0)
    private_rows(s, [] if row_type == "revoked" else [owned, dict(owned)])
    monkeypatch.setattr(public_client, "fetch_public_detail",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("PUBLIC GET")))
    try:
        with pytest.raises(ProviderError, match="non attestée"):
            s.enrich_owned_direct_public_detail(s._normalize(owned))
    finally:
        s.close()


def test_wrong_account_is_rejected_before_inventory_or_any_anonymous_http(monkeypatch):
    alice, bob = session("alice"), session("bob")
    called = []
    bob._rows = lambda **kw: called.append("PRIVATE") or (_ for _ in ()).throw(AssertionError)
    monkeypatch.setattr(public_client, "fetch_public_detail",
        lambda *a, **kw: called.append("PUBLIC") or (_ for _ in ()).throw(AssertionError))
    try:
        with pytest.raises(ProviderError, match="autre connexion"):
            bob.enrich_owned_direct_public_detail(alice._normalize(card(0)))
        assert called == []
    finally:
        alice.close()
        bob.close()


def test_conflicting_public_book_title_blocks_every_projected_field(monkeypatch):
    s = session()
    row = card(0)
    private_rows(s, [row])
    monkeypatch.setattr(public_client, "fetch_public_detail",
        lambda *a, **kw: public_detail(s._normalize(row), title="Another product entirely"))
    try:
        with pytest.raises(ProviderCapabilityError, match="title_conflict") as exc:
            s.enrich_owned_direct_public_detail(s._normalize(row))
        assert PRIVATE not in str(exc.value)
    finally:
        s.close()


@pytest.mark.parametrize("field,value", [
    ("public_url_evidence", "caller-supplied"),
    ("format_unverified", False),
    ("public_enrichment", {"spoofed": True}),
])
def test_merge_refuses_unqualified_owned_product_provenance(field, value):
    s = session()
    try:
        owned = s._normalize(card(0))
        meta = {**owned.metadata, field: value}
        forged = replace(owned, metadata=meta)
        with pytest.raises(ProviderCapabilityError, match="evidence_conflict"):
            merge_attested_owned_direct_public_detail(forged, public_detail(owned))
    finally:
        s.close()


def test_detail_without_matching_canonical_product_url_is_rejected():
    s = session()
    try:
        owned = s._normalize(card(0))
        another = s._normalize(card(1))
        with pytest.raises(ProviderCapabilityError, match="evidence_conflict"):
            merge_attested_owned_direct_public_detail(owned, public_detail(another))
    finally:
        s.close()


def test_manual_direct_enrichment_does_not_change_legacy_listing_requirement():
    s = session()
    try:
        owned = s._normalize(card(0))
        detail = public_detail(owned)
        with pytest.raises(ProviderCapabilityError, match="evidence_conflict"):
            merge_attested_owned_public_detail(owned, detail)
    finally:
        s.close()
