"""#3252: Kobo manifest reads cannot complete under another session generation.

No HTTP, token requests, purchases, local file mutation or Core scheduler.
"""
from __future__ import annotations

import pytest

from cifereca.plugin_api import Account, ProviderError
from test_kobo_manifest_account_binding_p1 import FakeVault, _session


@pytest.mark.parametrize("entrypoint", ["library", "backup_manifest"])
@pytest.mark.parametrize("race", ["disconnect", "same_account_reconnect", "account_replaced"])
def test_inflight_manifest_rejects_stale_account_or_epoch(entrypoint, race):
    vault = FakeVault([{
        "product_id": "synthetic-book", "account_id": "alice",
        "owned": True, "downloadable": True,
    }])
    session = _session(vault)
    initial_account = session._accounts[0]
    initial_generation = session._personal_binding_generation
    original_fetch = vault.library_manifest

    def race_after_http():
        rows = original_fetch()
        if race == "disconnect":
            session.close()
        elif race == "same_account_reconnect":
            # Model a completed reconnect to the SAME account identity, an
            # ABA that ordinary account-ID comparisons cannot detect.
            session._personal_binding_generation += 1
        else:
            session._accounts = [Account(
                unique_id="alice", display_name="Reconnected account",
                provider_id="kobo", credential_id=session.credential_id,
            )]
        return rows

    vault.library_manifest = race_after_http
    if entrypoint == "library":
        session.normalize_item = lambda row, **kw: row
    try:
        with pytest.raises(ProviderError, match="session modifiée pendant lecture"):
            getattr(session, entrypoint)()
        assert vault.calls == 1
        assert session.last_library_rejections == []
        if entrypoint == "backup_manifest":
            assert session.backup_manifest_rejection_count == 0
            assert session.backup_manifest_policy["manifest_authoritative"] is False
            assert session.backup_manifest_policy["acquisition_authorized"] is False
        if race == "same_account_reconnect":
            assert session.connected is True
            assert session._accounts[0] is initial_account
            assert session._personal_binding_generation == initial_generation + 1
    finally:
        session.close()


@pytest.mark.parametrize("entrypoint", ["library", "backup_manifest"])
def test_stable_personal_account_still_returns_exact_positive_manifest(entrypoint):
    vault = FakeVault([{
        "product_id": "synthetic-book", "account_id": "alice",
        "owned": True, "downloadable": True,
    }])
    session = _session(vault)
    session.normalize_item = lambda row, **kw: row
    try:
        rows = getattr(session, entrypoint)()
        assert len(rows) == 1
        assert rows[0]["product_id"] == "synthetic-book"
        assert vault.calls == 1
        if entrypoint == "backup_manifest":
            assert session.backup_manifest_policy["status"] == "classified"
    finally:
        session.close()
