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
            broker.accounts = lambda: [
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
