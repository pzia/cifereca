"""P0 #5851/#5975: a queued manual history GET retains its exact source lease.

This exercises the installed provider-neutral lease with synthetic persisted
owner rows, not a GTK selection or a provider-specific branch. A simple display
switch may hide a result but cannot retarget or revoke the admitted source.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from cifereca.core.library_projection import refresh_history
from cifereca.core.private_inventory_binding import private_inventory_binding_current
from cifereca.models import Account, HistoryItem
from cifereca.providers import ProviderConfigurationError
from cifereca.services import LibraryService


def _service():
    account = Account(
        unique_id="borrower-1", provider_id="unknown-supplier",
        credential_id="credential-1", identity_id="alice",
    )
    other = Account(
        unique_id="borrower-2", provider_id="other-supplier",
        credential_id="credential-2", identity_id="alice",
    )
    result = HistoryItem(
        id="h-1", provider="unknown-supplier", medium="digital",
        record_id="edition", title="Livre attesté",
        source_provider="unknown-supplier", source_credential="credential-1",
        source_account_id="borrower-1", identity_id="alice",
    )
    state = {"owner": "alice", "account_bound": True, "enabled": True}
    fetched, written = [], []

    def read(_account):
        fetched.append(_account)
        return [result]

    session = NS(
        provider_id=account.provider_id,
        credential_id=account.credential_id,
        connected=True, _closed=False, accounts=[account],
        capabilities=NS(history=True), history=read,
    )
    storage = NS(
        cache_get=lambda *_args: None,
        cache_put=lambda *args: written.append(args),
        identity_for_connection=lambda provider, credential: (
            state["owner"]
            if (provider, credential) == (account.provider_id, account.credential_id)
            else None
        ),
        provider_connections=lambda identity_id: [{
            "provider_id": account.provider_id,
            "credential_id": account.credential_id,
            "enabled": state["enabled"],
        }] if identity_id == "alice" else [],
        provider_scope_account_bound=lambda **_kwargs: state["account_bound"],
    )
    service = NS(
        accounts=[account], current_account=account,
        _account_context_epoch=10, _source_context_epoch=20,
        _sessions={(account.provider_id, account.credential_id): session},
        _session_key=lambda provider, credential: (provider, credential),
        identity_by_id=lambda identity_id: (
            NS(identity_id="alice") if identity_id == "alice" else None
        ),
        storage=storage,
        config=NS(get=lambda _key, default: default),
        diagnostics=NS(add=lambda *_args, **_kwargs: None),
        last_history=["previous presentation"],
        _session_for_account=lambda _account: session,
        # Manual History is not an automatic-Sync opt-in. This must never be
        # invoked when require_source_enabled=False.
        sync_source_enabled=lambda *_args: (_ for _ in ()).throw(
            AssertionError("manual history consulted Sync consent")
        ),
    )
    service._history_source_lease_current = (
        lambda account, source, *, scope, epoch:
        LibraryService._history_source_lease_current(
            service, account, source, scope=scope, epoch=epoch,
        )
    )
    return service, account, other, session, state, result, fetched, written


def test_manual_history_accepts_unknown_provider_exact_binding_without_sync_opt_in():
    service, account, _other, _session, _state, result, fetched, written = _service()
    assert refresh_history(service, force=True, account=account) == [result]
    assert fetched == [account]
    assert len(written) == 1 and written[0][0] == "account"
    assert written[0][1].startswith("history-v4:")
    assert service.last_history == [result]


@pytest.mark.parametrize("revocation", [
    "session_replaced", "source_epoch_changed", "account_unbound",
    "connection_disabled", "owner_changed", "account_removed",
    "disconnected", "borrower_changed",
])
def test_inflight_real_revocation_rejects_private_history_before_cache(
    revocation,
):
    service, account, _other, session, state, _result, fetched, written = _service()

    def revoke(_account):
        fetched.append(_account)
        if revocation == "session_replaced":
            replacement = NS(**vars(session))
            service._sessions[(account.provider_id, account.credential_id)] = replacement
        elif revocation == "source_epoch_changed":
            service._source_context_epoch += 1
        elif revocation == "account_unbound":
            state["account_bound"] = False
        elif revocation == "connection_disabled":
            state["enabled"] = False
        elif revocation == "owner_changed":
            state["owner"] = "bob"
        elif revocation == "account_removed":
            service.accounts = []
        elif revocation == "disconnected":
            session.connected = False
        elif revocation == "borrower_changed":
            account.unique_id = "forged-borrower"
        return [HistoryItem(
            id="stale", provider="unknown-supplier",
            medium="digital", record_id="edition", title="Stale",
        )]

    session.history = revoke
    with pytest.raises(ProviderConfigurationError, match="history_source_binding_changed"):
        refresh_history(service, force=True, account=account)
    assert len(fetched) == 1
    assert written == []
    assert service.last_history == ["previous presentation"]


@pytest.mark.parametrize("pre_revocation", [
    "session_replaced", "source_epoch_changed", "account_unbound",
    "connection_disabled", "owner_changed", "account_removed",
    "disconnected",
])
def test_pre_read_revocation_blocks_provider_history_get(pre_revocation):
    service, account, _other, session, state, _result, fetched, written = _service()
    if pre_revocation == "session_replaced":
        service._sessions[(account.provider_id, account.credential_id)] = NS(**vars(session))
    elif pre_revocation == "source_epoch_changed":
        service._source_context_epoch += 1
    elif pre_revocation == "account_unbound":
        state["account_bound"] = False
    elif pre_revocation == "connection_disabled":
        state["enabled"] = False
    elif pre_revocation == "owner_changed":
        state["owner"] = "bob"
    elif pre_revocation == "account_removed":
        service.accounts = []
    elif pre_revocation == "disconnected":
        session.connected = False
    # The lease is constructed when the already-admitted work begins.
    # A fresh epoch at admission is fine; the other durable fences still
    # reject real revocation before network I/O.
    if pre_revocation == "source_epoch_changed":
        account.credential_id = "reassigned-credential"
    with pytest.raises(ProviderConfigurationError, match="history_source_binding_unavailable"):
        refresh_history(service, force=True, account=account)
    assert not fetched and not written
    assert service.last_history == ["previous presentation"]


def test_display_only_account_change_keeps_original_history_read_and_exact_cache():
    service, account, other, session, _state, result, fetched, written = _service()

    def read(admitted):
        fetched.append(admitted)
        service.current_account = other
        service._account_context_epoch += 1
        return [result]

    session.history = read
    assert refresh_history(service, force=True, account=account) == [result]
    assert fetched == [account] and len(written) == 1
    assert service.last_history == ["previous presentation"]
    assert written[0][1] != (
        __import__("cifereca.core.library_projection", fromlist=["_history_cache_key"])
        ._history_cache_key(other)
    )


def test_automatic_inventory_lease_still_requires_sync_switch_and_strict_mode():
    service, account, _other, session, _state, _result, _fetched, _written = _service()
    scope = (
        account.identity_id, account.provider_id,
        account.credential_id, account.unique_id,
    )
    assert private_inventory_binding_current(
        service, account, session, scope=scope, epoch=20,
        require_source_enabled=False,
    ) is True
    # Existing callers keep the default source-enabled guard unchanged.
    assert private_inventory_binding_current(
        service, account, session, scope=scope, epoch=20,
    ) is False
    assert private_inventory_binding_current(
        service, account, session, scope=scope, epoch=20,
        require_source_enabled="false",
    ) is False
