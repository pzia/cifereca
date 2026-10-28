"""#3252 Kobo: no second private Vault manifest GET after Core-owner rebind.

Real KoboVaultClient pagination code + actual KoboSession; only a fake
library_manifest_page transport, no HTTP, token exchange, ADEPT or SQLite.
"""
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from cifereca.plugin_api import ProviderError
from cifereca_kobo_vault import KoboVaultClient
from tests.test_p1_3252_rights_observation_snapshot import _rig


def prepared():
    session, fake, broker, account = _rig([])
    vault = KoboVaultClient("https://vault.example", "synthetic-token")
    session.client = vault
    return session, broker, account, vault


def one_page(book):
    return {
        "product_id": book, "account_id": "alice",
        "owned": True, "downloadable": True, "title": book,
    }


@pytest.mark.parametrize("reader", [
    "rights_observation_snapshot", "library", "backup_manifest",
])
@pytest.mark.parametrize("race", [
    "broker_owner_changed", "broker_removed", "broker_same_owner_aba",
    "credential_v2_rebound", "account_same_scope_aba", "account_identity_rebound",
    "session_generation_rebound",
])
def test_kobo_real_manifest_stops_before_page_2_if_scope_changes(reader, race):
    session, broker, account, vault = prepared()
    page_calls = []

    def fetch(*, cursor=None, limit=200):
        page_calls.append(cursor)
        if cursor is None:
            if race == "broker_owner_changed":
                broker.identities = ("identity-B",)
            elif race == "broker_removed":
                broker.identities = ()
            elif race == "broker_same_owner_aba":
                session.identity_broker = type(broker)(("identity-A",))
            elif race == "credential_v2_rebound":
                broker.accounts = lambda: [
                    NS(provider_id="kobo", account_id="alice",
                       identity_id="identity-A", credential_id="foreign",
                       scope_version=2),
                ]
            elif race == "account_same_scope_aba":
                session._accounts = [replace(session._accounts[0])]
            elif race == "account_identity_rebound":
                session._accounts[0].identity_id = "identity-B"
            else:
                session._personal_binding_generation += 1
        return {
            "items": [one_page("book-1")],
            "next_cursor": "second" if cursor is None else None,
        }

    vault.library_manifest_page = fetch
    try:
        with pytest.raises(ProviderError):
            if reader == "rights_observation_snapshot":
                session.rights_observation_snapshot(account)
            else:
                getattr(session, reader)()
        # The original GET may already have happened, but an unauthorized
        # second GET must not be issued under the reassigned Core owner.
        assert page_calls == [None]
    finally:
        session.close()


@pytest.mark.parametrize("reader", [
    "rights_observation_snapshot", "library", "backup_manifest",
])
def test_kobo_stable_real_vault_pagination_still_reads_two_pages(reader):
    session, broker, account, vault = prepared()
    page_calls = []

    def fetch(*, cursor=None, limit=200):
        page_calls.append(cursor)
        return {
            "items": [one_page("book-1" if cursor is None else "book-2")],
            "next_cursor": "second" if cursor is None else None,
        }

    vault.library_manifest_page = fetch
    try:
        if reader == "rights_observation_snapshot":
            result = session.rights_observation_snapshot(account)
            assert [(x.edition_id, x.kind) for x in result.rights] == [
                ("book-1", "purchase"), ("book-2", "purchase"),
            ]
            assert result.status == result.coverage == "partial"
            assert not result.authorizes_negative_reconciliation
        else:
            result = getattr(session, reader)()
            assert len(result) == 2
        assert page_calls == [None, "second"]
    finally:
        session.close()


@pytest.mark.parametrize("reader", [
    "rights_observation_snapshot", "library", "backup_manifest",
])
@pytest.mark.parametrize("phase", ["get", "projection"])
def test_kobo_one_page_legacy_vault_cannot_publish_rebound_live_identity(
    reader, phase,
):
    """Even a 1-page injected Vault must not exploit a mutable Account ID."""
    session, vault, broker, account = _rig([
        one_page("book-1"),
    ])
    original_get = vault.library_manifest
    original_normalize = session.normalize_item

    def mutate_identity():
        session._accounts[0].identity_id = "identity-B"

    def during_get():
        rows = original_get()
        if phase == "get":
            mutate_identity()
        return rows

    def during_projection(*args, **kwargs):
        result = original_normalize(*args, **kwargs)
        if phase == "projection":
            mutate_identity()
        return result

    vault.library_manifest = during_get
    session.normalize_item = during_projection
    if phase == "projection" and reader == "backup_manifest":
        # Backups classify raw rows without normalize_item; inject the race
        # in its existing cover projection instead, without extra GETs.
        original_policy = session.catalog.cover_url
        def cover_rebind(row):
            mutate_identity()
            return original_policy(row)
        session.catalog.cover_url = cover_rebind
    try:
        with pytest.raises(ProviderError, match="session modifiée pendant lecture"):
            if reader == "rights_observation_snapshot":
                session.rights_observation_snapshot(account)
            else:
                getattr(session, reader)()
        assert vault.calls == 1
    finally:
        session.close()
