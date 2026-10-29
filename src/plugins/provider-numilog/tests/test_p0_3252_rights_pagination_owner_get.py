"""#3252: no second private MesLivres page GET under a foreign Core owner.

Live provider adapter with synthetic page HTML, broker and Account; no network,
acquisition, signed delivery, enrichment, SQLite or entitlement write.
"""
from dataclasses import replace
from types import SimpleNamespace

import pytest

import cifereca_numilog as provider
from cifereca.plugin_api import ProviderCapabilityError
from tests.test_p1_3252_rights_observation_snapshot import (
    bound_account, owned_row, session,
)


@pytest.mark.parametrize("phase", ["response", "parse"])
@pytest.mark.parametrize("race", [
    "owner_B", "owner_missing", "broker_ABA", "same_owner_duplicate",
    "live_account_ABA", "live_account_credential",
])
def test_numilog_rights_pagination_stops_after_first_page_owner_change(
    monkeypatch, phase, race,
):
    s, broker = session()
    pages = []
    next_url = s.library_url + "?page=2"

    def rebind():
        if race == "owner_B":
            broker.owner = "identity-B"
        elif race == "owner_missing":
            broker.owner = None
        elif race == "broker_ABA":
            newer = type(broker)()
            newer.account_id = broker.account_id
            s.identity_broker = newer
        elif race == "same_owner_duplicate":
            broker.accounts = lambda identity_id=None: [
                SimpleNamespace(provider_id="numilog", account_id=broker.account_id,
                                identity_id="identity-A"),
                SimpleNamespace(provider_id="numilog", account_id=broker.account_id,
                                identity_id="identity-A"),
            ]
        elif race == "live_account_ABA":
            s._accounts = [replace(s._accounts[0])]
        else:
            s._accounts[0].credential_id = "different-credential"

    def page(url, **_):
        pages.append(url)
        if url == s.library_url and phase == "response":
            rebind()
        return (url, "PAGE1" if url == s.library_url else "PAGE2")

    def parse_page(html, _url, **_):
        if html == "PAGE1":
            if phase == "parse":
                rebind()
            return ([owned_row()], next_url)
        return ([{**owned_row(), "lid": "43"}], None)

    s._page = page
    monkeypatch.setattr(provider, "parse_library_page", parse_page)
    try:
        with pytest.raises(ProviderCapabilityError, match="identité propriétaire modifiée"):
            s.rights_observation_snapshot(bound_account(s))
        # The changed owner must not trigger the second private GET.
        assert pages == [s.library_url]
    finally:
        s.close()


def test_numilog_stable_typed_rights_pagination_keeps_two_positive_credits(monkeypatch):
    s, _ = session()
    pages = []
    next_url = s.library_url + "?page=2"

    def page(url, **_):
        pages.append(url)
        return (url, "PAGE1" if url == s.library_url else "PAGE2")

    def parse_page(html, _url, **_):
        return (
            [owned_row()] if html == "PAGE1"
            else [{**owned_row(), "lid": "43", "title": "Second edition"}],
            next_url if html == "PAGE1" else None,
        )

    s._page = page
    monkeypatch.setattr(provider, "parse_library_page", parse_page)
    try:
        snap = s.rights_observation_snapshot(bound_account(s))
        assert pages == [s.library_url, next_url]
        assert len(snap.rights) == 2
        assert {right.edition_id for right in snap.rights} == {
            s._id(owned_row()), s._id({**owned_row(), "lid": "43"}),
        }
        assert snap.status == snap.coverage == "partial"
        assert not snap.authorizes_negative_reconciliation
        assert not snap.authorizes_mutation
    finally:
        s.close()


def test_unverified_broker_cannot_trigger_any_typed_private_page_get(monkeypatch):
    s, broker = session()
    broker.owner = None
    calls = []
    s._page = lambda url, **_: calls.append(url)
    monkeypatch.setattr(provider, "parse_library_page", lambda *_a, **_k: ([], None))
    try:
        with pytest.raises(ProviderCapabilityError, match="propriétaire unique"):
            s.rights_observation_snapshot(bound_account(s))
        assert calls == []
    finally:
        s.close()


@pytest.mark.parametrize("phase", ["response", "parse"])
@pytest.mark.parametrize("race", [
    "owner_B", "owner_missing", "broker_ABA", "same_owner_duplicate",
    "live_account_ABA", "live_account_credential",
])
def test_targeted_numilog_delivery_card_never_gets_next_page_after_rebind(
    monkeypatch, phase, race,
):
    """Explicit selected-card delivery must not read page two under new owner."""
    s, broker = session()
    seen = []
    next_url = s.library_url + "?page=2"
    selected_id = s._id(owned_row())

    def rebind():
        if race == "owner_B":
            broker.owner = "identity-B"
        elif race == "owner_missing":
            broker.owner = None
        elif race == "broker_ABA":
            new_broker = type(broker)()
            new_broker.account_id = broker.account_id
            s.identity_broker = new_broker
        elif race == "same_owner_duplicate":
            broker.accounts = lambda identity_id=None: [
                SimpleNamespace(provider_id="numilog", account_id=broker.account_id,
                                identity_id="identity-A"),
                SimpleNamespace(provider_id="numilog", account_id=broker.account_id,
                                identity_id="identity-A"),
            ]
        elif race == "live_account_ABA":
            s._accounts = [replace(s._accounts[0])]
        else:
            s._accounts[0].credential_id = "different-credential"

    def page(url, **_):
        seen.append(url)
        if url == s.library_url and phase == "response":
            rebind()
        return url, "PAGE1" if url == s.library_url else "PAGE2"

    def parse_page(html, _url, **_):
        if html == "PAGE1":
            if phase == "parse":
                rebind()
            return [owned_row()], next_url
        return [{**owned_row(), "lid": "43"}], None

    s._page = page
    monkeypatch.setattr(provider, "parse_library_page", parse_page)
    try:
        with pytest.raises(ProviderCapabilityError, match="livraison arrêtée"):
            s._rows(include_delivery=True, delivery_record_id=selected_id)
        assert seen == [s.library_url]
    finally:
        s.close()


def test_targeted_numilog_delivery_with_stable_owner_paginates_normally(monkeypatch):
    s, _ = session()
    seen = []
    next_url = s.library_url + "?page=2"
    s._page = lambda url, **_: (seen.append(url) or
                               (url, "PAGE1" if url == s.library_url else "PAGE2"))
    monkeypatch.setattr(
        provider, "parse_library_page",
        lambda page, _url, **_: (
            [owned_row()] if page == "PAGE1" else
            [{**owned_row(), "lid": "43"}],
            next_url if page == "PAGE1" else None,
        ),
    )
    try:
        rows = s._rows(
            include_delivery=True, delivery_record_id=s._id(owned_row()),
        )
        assert len(rows) == 2
        assert seen == [s.library_url, next_url]
    finally:
        s.close()


def test_targeted_delivery_with_unverified_broker_refuses_first_get(monkeypatch):
    s, broker = session()
    broker.owner = None
    seen = []
    s._page = lambda url, **_: seen.append(url)
    monkeypatch.setattr(provider, "parse_library_page",
                        lambda *_a, **_k: ([], None))
    try:
        with pytest.raises(ProviderCapabilityError, match="propriétaire unique"):
            s._rows(include_delivery=True, delivery_record_id=s._id(owned_row()))
        assert seen == []
    finally:
        s.close()


def test_brokerless_legacy_targeted_delivery_remains_readable(monkeypatch):
    s, _ = session(with_broker=False)
    seen = []
    s._page = lambda url, **_: (seen.append(url) or (url, "PAGE"))
    monkeypatch.setattr(provider, "parse_library_page",
                        lambda *_a, **_k: ([owned_row()], None))
    try:
        assert s._rows(
            include_delivery=True, delivery_record_id=s._id(owned_row()),
        ) == [owned_row()]
        assert seen == [s.library_url]
    finally:
        s.close()


@pytest.mark.parametrize("phase", ["response", "parse"])
@pytest.mark.parametrize("race", [
    "owner_B", "owner_missing", "broker_ABA", "same_owner_duplicate",
    "live_account_ABA", "live_account_credential",
])
def test_legacy_numilog_library_no_second_private_get_after_rebind(
    monkeypatch, phase, race,
):
    """Legacy positive inventory is not a license for an in-flight foreign GET."""
    s, broker = session()
    next_url = s.library_url + "?page=2"
    seen = []

    def rebind():
        if race == "owner_B":
            broker.owner = "identity-B"
        elif race == "owner_missing":
            broker.owner = None
        elif race == "broker_ABA":
            other = type(broker)()
            other.account_id = broker.account_id
            s.identity_broker = other
        elif race == "same_owner_duplicate":
            broker.accounts = lambda identity_id=None: [
                SimpleNamespace(provider_id="numilog", account_id=broker.account_id,
                                identity_id="identity-A"),
                SimpleNamespace(provider_id="numilog", account_id=broker.account_id,
                                identity_id="identity-A"),
            ]
        elif race == "live_account_ABA":
            s._accounts = [replace(s._accounts[0])]
        else:
            s._accounts[0].credential_id = "different-credential"

    def page(url, **_):
        seen.append(url)
        if url == s.library_url and phase == "response":
            rebind()
        return url, "PAGE1" if url == s.library_url else "PAGE2"

    def parse_page(html, _url, **_):
        if html == "PAGE1":
            if phase == "parse":
                rebind()
            return [owned_row()], next_url
        return [{**owned_row(), "lid": "43"}], None

    s._page = page
    monkeypatch.setattr(provider, "parse_library_page", parse_page)
    try:
        with pytest.raises(ProviderCapabilityError, match="inventaire arrêté"):
            s.library()
        assert seen == [s.library_url]
    finally:
        s.close()


@pytest.mark.parametrize("owners", [
    ("identity-A", "identity-B"),
    ("identity-A", "identity-A"),
    (),
])
def test_legacy_unverified_owner_stays_readable_but_not_transactional(
    monkeypatch, owners,
):
    """Existing partial legacy inventory may be inspected, never attested."""
    s, broker = session()
    broker.accounts = lambda identity_id=None: [
        SimpleNamespace(provider_id="numilog", account_id=broker.account_id,
                        identity_id=identity)
        for identity in owners
    ]
    seen = []
    next_url = s.library_url + "?page=2"
    s._page = lambda url, **_: (seen.append(url) or
                               (url, "PAGE1" if url == s.library_url else "PAGE2"))
    monkeypatch.setattr(
        provider, "parse_library_page",
        lambda page, _url, **_: (
            [owned_row()] if page == "PAGE1" else
            [{**owned_row(), "lid": "43"}],
            next_url if page == "PAGE1" else None,
        ),
    )
    try:
        items = s.library()
        assert len(items) == 2
        assert seen == [s.library_url, next_url]
        assert all(row.source_provider == "numilog" for row in items)
    finally:
        s.close()


def test_legacy_brokerless_inventory_still_reads_private_owned_cards(monkeypatch):
    s, _ = session(with_broker=False)
    seen = []
    s._page = lambda url, **_: (seen.append(url) or (url, "PAGE"))
    monkeypatch.setattr(provider, "parse_library_page",
                        lambda *_a, **_k: ([owned_row()], None))
    try:
        assert len(s.library()) == 1
        assert seen == [s.library_url]
    finally:
        s.close()
