"""#3197: legacy direct loan reads must be scoped *after* provider I/O.

Synthetic sessions only. No provider HTTP, DRM, acquisition or return.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from cifereca.providers import ProviderConfigurationError
from cifereca.services import _loan_cache_key
from tests.test_core_p1_3119_3122_opening_typed_inventory import account, loan
from tests.test_sync_state_contract import _service


@pytest.mark.parametrize("mutation", [
    "identity", "borrower", "epoch", "session_swap",
    "disconnect", "credential", "provider",
])
def test_legacy_loan_read_rejects_rebind_before_publishing_cache(
    tmp_path, mutation,
):
    service = _service(tmp_path)
    selected = account()
    service.accounts = [selected]
    source = loan(selected)
    key = _loan_cache_key(selected)
    calls = []
    session = NS(
        provider_id=selected.provider_id,
        credential_id=selected.credential_id,
        connected=True,
        capabilities=NS(loans=True),
        close=lambda: None,
    )
    binding = service._session_key(selected.provider_id, selected.credential_id)
    service._sessions[binding] = session
    initial_epoch = service._source_context_epoch
    original_scope = (
        selected.identity_id, selected.provider_id,
        selected.credential_id, selected.unique_id,
    )
    original_session = (
        session.provider_id, session.credential_id, session.connected,
    )

    def fetch(current):
        calls.append(current)
        if mutation == "identity":
            selected.identity_id = "foreign"
        elif mutation == "borrower":
            selected.unique_id = "other-borrower"
        elif mutation == "epoch":
            service._source_context_epoch += 1
        elif mutation == "session_swap":
            service._sessions[binding] = NS(
                connected=True, provider_id=session.provider_id,
                credential_id=session.credential_id,
            )
        elif mutation == "disconnect":
            session.connected = False
        elif mutation == "credential":
            session.credential_id = "other-credential"
        elif mutation == "provider":
            session.provider_id = "other-provider"
        return [source]

    session.loans = fetch
    try:
        with pytest.raises(ProviderConfigurationError, match="loan_inventory_scope_changed"):
            service._loans_for_account(selected, force=True)
        assert calls == [selected]
        assert service.storage.cache_get("account", key) is None
        assert service.storage.entitlements() == []
        assert source.identity_id == original_scope[0]
    finally:
        (selected.identity_id, selected.provider_id,
         selected.credential_id, selected.unique_id) = original_scope
        service._source_context_epoch = initial_epoch
        (session.provider_id, session.credential_id,
         session.connected) = original_session
        service._sessions[binding] = session
        service.close()


def test_unchanged_legacy_reader_still_reads_once_and_caches_only_positives(tmp_path):
    service = _service(tmp_path)
    selected = account()
    service.accounts = [selected]
    session = NS(
        provider_id=selected.provider_id,
        credential_id=selected.credential_id,
        connected=True, capabilities=NS(loans=True), close=lambda: None,
    )
    calls = []
    session.loans = lambda current: calls.append(current) or [loan(selected)]
    service._sessions[
        service._session_key(selected.provider_id, selected.credential_id)
    ] = session
    try:
        found = service._loans_for_account(selected, force=True)
        assert calls == [selected] and len(found) == 1
        assert service.storage.cache_get("account", _loan_cache_key(selected))[0]["id"] == found[0].id
        assert not service.storage.entitlements()
    finally:
        service.close()
