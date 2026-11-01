"""#981: a source Sync reuses exactly one authenticated Kobo manifest.

All responses are synthetic httpx.MockTransport. No account, acquisition or
filesystem operation is performed. Outside the explicit batch, reads stay fresh.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

import httpx
import pytest

from cifereca.plugin_api import Account, ProviderError
from cifereca_kobo import DESCRIPTOR, KoboSession
from cifereca_kobo_vault import KoboVaultClient, KoboVaultError


def _session(handler):
    account = Account(
        identity_id="reader-a", provider_id="kobo",
        credential_id="credential-a", unique_id="vault-account-a",
    )
    session = KoboSession(
        descriptor=DESCRIPTOR, credential_id=account.credential_id,
        label="Kobo", credentials={"api_token": "fixture"},
        options={"base_url": "https://vault.example"},
        config={}, storage={}, diagnostics=NS(add=lambda *a, **k: None),
    )
    session.client.close()
    session.client = KoboVaultClient(
        "https://vault.example", "fixture",
        transport=httpx.MockTransport(handler),
    )
    session.connected = True
    session._accounts = [account]
    session.identity_broker = NS(
        accounts=lambda identity: [
            NS(scope_version=2, provider_id="kobo", account_id=account.unique_id,
               credential_id=account.credential_id, identity_id=identity),
        ] if identity == "reader-a" else [],
    )
    return session, account


def test_source_batch_reuses_two_pages_then_next_sync_fetches_fresh():
    calls = []

    def handler(request):
        assert request.url.path == "/api/v2/library/manifest"
        calls.append(request.url.params.get("cursor"))
        if request.url.params.get("cursor") == "p2":
            return httpx.Response(200, json={"items": [
                {"product_id": "book-2", "title": "Second", "owned": True},
            ], "next_cursor": None})
        return httpx.Response(200, json={"items": [
            {"product_id": "book-1", "title": "First", "owned": True},
        ], "next_cursor": "p2"})

    session, account = _session(handler)
    try:
        with session.source_sync_inventory(account):
            rows = session.library()
            assert len(rows) == 2
            snapshot = session.downloadable_resource_snapshot(account)
            assert len(snapshot.resources) == 2
            assert calls == [None, "p2"]
            # Core's mirror must not be able to mutate the retained provider
            # evidence subsequently consumed by its canonical rights reader.
            rows[0].metadata["owned"] = False
            assert len(session.downloadable_resource_snapshot(account).resources) == 2
            assert calls == [None, "p2"]
        session.library()  # a separate user action is always a fresh GET
        assert calls == [None, "p2", None, "p2"]
        with session.source_sync_inventory(account):
            session.library()
            session.downloadable_resource_snapshot(account)
        assert calls == [None, "p2", None, "p2", None, "p2"]
    finally:
        session.close()


def test_source_batch_refuses_owner_or_generation_switch_without_second_get():
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(200, json={"items": [
            {"product_id": "book-a", "owned": True},
        ], "next_cursor": None})

    session, account = _session(handler)
    try:
        with session.source_sync_inventory(account):
            assert len(session.library()) == 1
            account.identity_id = "reader-b"
            with pytest.raises(ProviderError):
                session.downloadable_resource_snapshot(account)
        assert len(calls) == 1
    finally:
        session.close()


def test_failed_page_is_never_cached_inside_source_batch():
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(200, json={"items": [
            {"product_id": "book-a", "owned": True},
        ], "next_cursor": False})

    session, account = _session(handler)
    try:
        with session.source_sync_inventory(account):
            with pytest.raises(KoboVaultError):
                session.library()
            with pytest.raises(KoboVaultError):
                session.library()
        assert len(calls) == 2
    finally:
        session.close()
