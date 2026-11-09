"""#4987: cross-profile scope is necessary, inert and fail-closed."""
from __future__ import annotations

from dataclasses import replace

import pytest

from cifereca.core.borrow_for_identity_action_scope import (
    capture_cross_profile_borrow_scope as capture,
)
from tests.test_core_p1_4662_live_lender_preview import _candidate, harness


def lease_for(service, account, session, *, notice=None):
    return capture(
        service, requesting_identity_id="default",
        borrowing_account=account,
        exact_notice=notice or _candidate().notice,
        lender_session=session,
    )


def test_active_requester_keeps_own_profile_and_pins_exact_lender(harness):
    service, account, session = harness
    requesting = service.current_identity
    lease = lease_for(service, account, session)
    assert lease is not None and lease.current(service)
    assert lease._account is account
    assert lease._session is session
    assert lease._requester is requesting
    assert "card-alex" not in repr(lease)
    assert "record-1" not in repr(lease)
    assert service.current_identity is requesting
    assert service.current_identity.identity_id == "default"
    assert session.calls == 0


@pytest.mark.parametrize("mutation", [
    "reader_context", "account_epoch", "source_epoch",
    "session_epoch", "session_rebind", "session_closed",
    "account_rebind", "account_fields", "notice_fields", "disabled",
    "foreign_owner", "duplicated_active", "duplicated_disabled",
])
def test_cross_profile_lease_revoked_without_provider_io(
    harness, monkeypatch, mutation,
):
    service, account, session = harness
    notice = _candidate().notice
    lease = lease_for(service, account, session, notice=notice)
    assert lease is not None
    if mutation == "reader_context":
        service.current_identity = replace(service.current_identity)
    elif mutation == "account_epoch":
        service._account_context_epoch += 2
    elif mutation == "source_epoch":
        service._source_context_epoch += 2
    elif mutation == "session_epoch":
        session._epoch += 1
    elif mutation == "session_rebind":
        service._sessions[service._session_key("paris", "card-alex")] = object()
    elif mutation == "session_closed":
        session.connected = False
    elif mutation == "account_rebind":
        service.accounts = [replace(account)]
    elif mutation == "account_fields":
        account.unique_id = "another"
    elif mutation == "notice_fields":
        notice.record_id = "another"
    elif mutation == "disabled":
        service.storage.provider_connection_put({
            "identity_id": "alex", "provider_id": "paris",
            "credential_id": "card-alex", "label": "disabled",
            "enabled": False, "metadata": {},
        })
    elif mutation == "foreign_owner":
        service.storage.provider_connection_put({
            "identity_id": "default", "provider_id": "paris",
            "credential_id": "card-alex", "label": "foreign",
            "enabled": True, "metadata": {},
        })
    else:
        original = service.storage.provider_connections

        def duplicate(*, identity_id):
            rows = original(identity_id=identity_id)
            if identity_id == "alex":
                existing = next(x for x in rows
                                if x.get("credential_id") == "card-alex")
                return [*rows, {**existing, "enabled":
                                mutation == "duplicated_active"}]
            return rows

        monkeypatch.setattr(service.storage, "provider_connections", duplicate)
    assert lease.current(service) is False
    assert session.calls == 0


def test_supplier_session_identity_is_not_equivalent_to_requesting_identity(harness):
    service, account, session = harness
    assert capture(
        service, requesting_identity_id="alex",
        borrowing_account=account, exact_notice=_candidate().notice,
        lender_session=session,
    ) is None
    assert capture(
        service, requesting_identity_id="unregistered",
        borrowing_account=account, exact_notice=_candidate().notice,
        lender_session=session,
    ) is None
    assert session.calls == 0


def test_lender_binding_rebound_during_callback_never_yields_live_lease(
    harness, monkeypatch,
):
    service, account, session = harness
    lease = lease_for(service, account, session)
    assert lease is not None
    original = service.storage.provider_connections

    def revoke_during_lookup(*, identity_id):
        rows = original(identity_id=identity_id)
        service._source_context_epoch += 1
        return rows

    monkeypatch.setattr(
        service.storage, "provider_connections", revoke_during_lookup,
    )
    assert lease.current(service) is False
    assert lease_for(service, account, session) is None
    assert session.calls == 0


@pytest.mark.parametrize("change", [
    "account", "session", "notice", "session_epoch", "reader_context",
])
def test_lender_storage_callback_rebinding_without_epoch_increment_is_revoked(
    harness, monkeypatch, change,
):
    service, account, session = harness
    notice = _candidate().notice
    lease = lease_for(service, account, session, notice=notice)
    assert lease is not None
    original = service.storage.provider_connections

    def changed_without_epoch(*, identity_id):
        rows = original(identity_id=identity_id)
        if change == "account":
            service.accounts = [replace(account)]
        elif change == "session":
            service._sessions[service._session_key(
                "paris", "card-alex",
            )] = object()
        elif change == "notice":
            notice.record_id = "another-edition"
        elif change == "session_epoch":
            session._epoch += 1
        else:
            service.current_identity = replace(service.current_identity)
        return rows

    monkeypatch.setattr(
        service.storage, "provider_connections", changed_without_epoch,
    )
    assert lease.current(service) is False
    assert session.calls == 0
