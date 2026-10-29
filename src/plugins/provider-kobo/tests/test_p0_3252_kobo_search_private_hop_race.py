"""P0 #3252: public Kobo discovery must not authorize a later private GET.

Exercises actual KoboSession/KoboCatalogService/KoboVaultClient with local
search_catalog/search_library fakes only; zero HTTP, token or acquisition.
"""
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from cifereca.plugin_api import ProviderError
from cifereca_kobo_vault import KoboVaultClient
from cifereca_kobo_catalog import KoboCatalogService
from tests.test_p1_3252_rights_observation_snapshot import _rig


def rig():
    session, fake, broker, account = _rig([])
    vault = KoboVaultClient("https://vault.example", "synthetic-token")
    session.client = vault
    session.catalog = KoboCatalogService(
        vault, credential_id="synthetic-card", provider_id="kobo",
    )
    calls = []

    def public(*args, **kwargs):
        calls.append("catalog")
        return {"items": [{"product_id": "book-1", "title": "Synthetic"}]}

    def private(*args, **kwargs):
        calls.append("library")
        return {"items": [
            {"product_id": "book-1", "title": "Synthetic", "account_id": "alice",
             "owned": True, "collection": "library"},
        ]}

    vault.search_catalog = public
    vault.search_library = private
    return session, broker, account, vault, calls


@pytest.mark.parametrize("race", [
    "owner_B", "broker_ABA", "broker_v2_credential", "live_account_ABA",
    "live_identity", "generation",
])
def test_kobo_search_never_starts_private_get_after_public_owner_rebind(race):
    session, broker, account, vault, calls = rig()
    original = vault.search_catalog

    def rebind(*args, **kwargs):
        result = original(*args, **kwargs)
        if race == "owner_B":
            broker.identities = ("identity-B",)
        elif race == "broker_ABA":
            session.identity_broker = type(broker)(("identity-A",))
        elif race == "broker_v2_credential":
            broker.accounts = lambda identity_id=None: [
                NS(provider_id="kobo", account_id="alice",
                   identity_id="identity-A", scope_version=2,
                   credential_id="foreign-credential"),
            ]
        elif race == "live_account_ABA":
            session._accounts = [replace(session._accounts[0])]
        elif race == "live_identity":
            session._accounts[0].identity_id = "foreign-owner"
        else:
            session._personal_binding_generation += 1
        return result

    vault.search_catalog = rebind
    try:
        with pytest.raises(ProviderError):
            session.search("Synthetic", scope="all")
        assert calls == ["catalog"]  # NO private /library/search
    finally:
        session.close()


def test_kobo_search_rebind_during_private_response_disallows_owned_hit():
    session, broker, account, vault, calls = rig()
    original = vault.search_library

    def rebind(*args, **kwargs):
        result = original(*args, **kwargs)
        broker.identities = ("identity-B",)
        return result

    vault.search_library = rebind
    try:
        with pytest.raises(ProviderError):
            session.search("Synthetic", scope="all")
        assert calls == ["catalog", "library"]
    finally:
        session.close()


def test_kobo_search_stable_public_and_private_results_still_merged():
    session, broker, account, vault, calls = rig()
    try:
        rows = session.search("Synthetic", scope="all")
        assert len(rows) == 1 and rows[0].record_id == "book-1"
        assert rows[0].owned is True
        assert rows[0].source_credential == "synthetic-card"
        assert calls == ["catalog", "library"]
    finally:
        session.close()


def test_kobo_explicit_public_search_never_requires_private_owner():
    session, broker, account, vault, calls = rig()
    broker.identities = ("identity-B",)
    try:
        result = session.search("Synthetic", scope="catalog")
        assert len(result) == 1
        assert result[0].owned is not True
        assert calls == ["catalog"]
    finally:
        session.close()
