"""#1072 P0 — an identity reassignment cannot publish an old purchase's details.

All private inventory and anonymous public transport are simulated. No account
login, live HTTP, cookies, signed ACSM, license, download or external service.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from cifereca.plugin_api import ProviderCapabilityError
from cifereca_numilog import NumilogSession
from cifereca_numilog_public import parse_public_search
from cifereca_numilog_public_detail import parse_public_detail_jsonld
import cifereca_numilog_public_client as client

HERE = Path(__file__).parent / "fixtures"
SEARCH_URL = ("https://www.numilog.com/Pages/Recherche/ResultatRecherche.aspx"
              "?mode=brSimple&query=jefferson")
PAGE = parse_public_search((HERE / "public_search_jefferson_20261016.html").read_text(),
                           SEARCH_URL)
PRIVATE = "SYNTHETIC_PRIVATE_IDENTITY_TOKEN"


class MutableBroker:
    def __init__(self):
        self.scope = None
        self.owner = "identity-alice"
        self.calls = 0

    def accounts(self):
        self.calls += 1
        return [] if self.owner is None else [
            SimpleNamespace(provider_id="numilog",
                            account_id="numilog:" + self.scope,
                            identity_id=self.owner),
        ]


def make_session():
    broker = MutableBroker()
    s = NumilogSession(
        credential_id="account-a", label="Numilog", credentials={},
        options={}, config={}, diagnostics=None, identity_broker=broker,
    )
    broker.scope = s.scope
    s.connected = True
    card = PAGE.items[0]
    row = {
        "lid": "42", "variant": "()", "title": card.title,
        "cover": None, "public_url": card.friendly_url,
    }
    s._rows = lambda **_kwargs: [row]
    return s, broker, row


def detail(row):
    node = {"@context": "https://schema.org", "@type": "Book",
            "url": row["public_url"], "name": row["title"],
            "author": {"@type": "Person", "name": "Auteur attesté"}}
    return parse_public_detail_jsonld(
        '<script type="application/ld+json">'
        + json.dumps(node, ensure_ascii=False) + "</script>",
        row["public_url"],
    )


def check_rejected(exc):
    assert "identité propriétaire" in str(exc)
    assert PRIVATE not in str(exc)
    assert "https://" not in str(exc)


def test_binding_absent_at_start_refuses_private_inventory_and_public_http(monkeypatch):
    s, broker, row = make_session()
    try:
        broker.owner = None
        seen = []
        s._rows = lambda **_kw: seen.append("private")
        monkeypatch.setattr(client, "fetch_public_detail",
                            lambda *_a: seen.append("public"))
        with pytest.raises(ProviderCapabilityError) as caught:
            s.enrich_owned_direct_public_detail(s._normalize(row))
        check_rejected(caught.value)
        assert seen == []
    finally:
        s.close()


@pytest.mark.parametrize("new_owner", [None, "identity-bob"])
def test_private_inventory_binding_change_blocks_public_fetch(monkeypatch, new_owner):
    s, broker, row = make_session()
    try:
        seen = []
        def private_rows(**_kw):
            seen.append("inventory")
            broker.owner = new_owner
            return [row]
        s._rows = private_rows
        monkeypatch.setattr(client, "fetch_public_search",
                            lambda *_a, **_kw: seen.append("public search"))
        monkeypatch.setattr(client, "fetch_public_detail",
                            lambda *_a: seen.append("public detail"))
        with pytest.raises(ProviderCapabilityError) as caught:
            s.enrich_owned_public_listing(s._normalize(row))
        check_rejected(caught.value)
        assert seen == ["inventory"]
    finally:
        s.close()


@pytest.mark.parametrize("method", [
    "enrich_owned_public_listing",
    "enrich_owned_public_detail",
    "enrich_owned_direct_public_detail",
])
def test_rebinding_during_remote_public_fetch_never_returns_prior_owner_row(
    monkeypatch, method,
):
    s, broker, row = make_session()
    seen = []
    def public_search(query, *, page, field):
        seen.append("public search")
        if method == "enrich_owned_public_listing":
            broker.owner = "identity-bob"
        return PAGE
    def public_detail(product_url):
        seen.append("public detail")
        broker.owner = "identity-bob"
        return detail(row)
    monkeypatch.setattr(client, "fetch_public_search", public_search)
    monkeypatch.setattr(client, "fetch_public_detail", public_detail)
    try:
        with pytest.raises(ProviderCapabilityError) as caught:
            getattr(s, method)(s._normalize(row))
        check_rejected(caught.value)
        assert seen == (["public search"] if method.endswith("listing")
                        else (["public detail"] if "direct" in method
                              else ["public search", "public detail"]))
    finally:
        s.close()


@pytest.mark.parametrize("method", [
    "enrich_owned_public_listing",
    "enrich_owned_public_detail",
    "enrich_owned_direct_public_detail",
])
def test_stable_owner_can_enrich_and_no_private_rights_are_reassigned(
    monkeypatch, method,
):
    s, broker, row = make_session()
    monkeypatch.setattr(client, "fetch_public_search",
                        lambda *_a, **_kw: PAGE)
    monkeypatch.setattr(client, "fetch_public_detail",
                        lambda *_a: detail(row))
    try:
        original = s._normalize(row)
        result = getattr(s, method)(original)
        assert result.record_id == original.record_id
        assert result.source_credential == "account-a"
        assert result.owned is True and result.downloadable is True
        assert result.download_url is None
        assert result.format is None
        assert broker.calls >= 2
    finally:
        s.close()


def test_broker_failure_mid_public_detail_does_not_expose_private_exception(monkeypatch):
    s, broker, row = make_session()
    def bad_detail(_url):
        broker.accounts = lambda: (_ for _ in ()).throw(
            RuntimeError("PRIVATE=" + PRIVATE))
        return detail(row)
    monkeypatch.setattr(client, "fetch_public_detail", bad_detail)
    try:
        with pytest.raises(ProviderCapabilityError) as caught:
            s.enrich_owned_direct_public_detail(s._normalize(row))
        check_rejected(caught.value)
    finally:
        s.close()


def test_legacy_missing_broker_keeps_explicit_account_record_id_guards(monkeypatch):
    s, _broker, row = make_session()
    s.identity_broker = None  # Historic standalone plugin clients.
    monkeypatch.setattr(client, "fetch_public_detail", lambda *_a: detail(row))
    try:
        result = s.enrich_owned_direct_public_detail(s._normalize(row))
        assert result.owned is True
        assert result.source_credential == "account-a"
        assert result.download_url is None
    finally:
        s.close()


def test_owner_rebinding_during_final_projection_refuses_old_edition(monkeypatch):
    """#1072: guard *after* field projection, not only after the HTTP GET."""
    import cifereca_numilog_enrichment as enrichment

    s, broker, row = make_session()
    monkeypatch.setattr(client, "fetch_public_detail", lambda *_a: detail(row))
    original = enrichment.merge_attested_owned_direct_public_detail

    def rebind_after_merge(*args):
        result = original(*args)
        broker.owner = "identity-bob"
        return result

    monkeypatch.setattr(enrichment, "merge_attested_owned_direct_public_detail",
                        rebind_after_merge)
    try:
        with pytest.raises(ProviderCapabilityError) as caught:
            s.enrich_owned_direct_public_detail(s._normalize(row))
        check_rejected(caught.value)
    finally:
        s.close()


def test_duplicate_same_identity_owner_is_rejected_before_private_enrichment(monkeypatch):
    """A repeated AccountRef is not proof of a uniquely bound credential."""
    s, broker, row = make_session()
    original_accounts = broker.accounts
    def ambiguous():
        return original_accounts() * 2
    broker.accounts = ambiguous
    seen = []
    s._rows = lambda **_kw: (seen.append("private") or [row])
    monkeypatch.setattr(client, "fetch_public_detail",
                        lambda *_a: (seen.append("public") or detail(row)))
    try:
        with pytest.raises(ProviderCapabilityError) as caught:
            s.enrich_owned_direct_public_detail(s._normalize(row))
        check_rejected(caught.value)
        assert seen == []
    finally:
        s.close()


@pytest.mark.parametrize("change", ["broker_replaced", "duplicate_owner"])
def test_same_identity_broker_replacement_or_duplicate_cannot_publish_metadata(
    monkeypatch, change,
):
    s, broker, row = make_session()
    visited = []

    def public_detail(product_url):
        visited.append("public")
        if change == "broker_replaced":
            newer = MutableBroker()
            newer.scope = broker.scope
            newer.owner = broker.owner  # same identity, different broker instance
            s.identity_broker = newer
        else:
            original_accounts = broker.accounts
            broker.accounts = lambda: original_accounts() * 2
        return detail(row)

    monkeypatch.setattr(client, "fetch_public_detail", public_detail)
    try:
        with pytest.raises(ProviderCapabilityError) as caught:
            s.enrich_owned_direct_public_detail(s._normalize(row))
        check_rejected(caught.value)
        assert visited == ["public"]
    finally:
        s.close()
