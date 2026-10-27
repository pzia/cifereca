"""#3121: no provider-defined scalar getters run during Core loan admission.

Uses the existing offline connected BnF loan fixture; no HTTP or ADEPT.
"""
from __future__ import annotations

import pytest

from cifereca.core.loan_action_scope import (
    _inert_session_state, capture_loan_action_scope,
)
from tests.test_core_p0_2055_bnf_explicit_stage import connected_core


def _capture(service, session, account, loan):
    return capture_loan_action_scope(
        service, loan, account, session, expected_identity_id="default",
    )


def test_normal_session_scalar_memory_is_inert_and_action_still_available(connected_core):
    service, session, account, loan, _state, downloaded = connected_core
    memory = _inert_session_state(session)
    assert memory is session.__dict__
    assert memory["provider_id"] == account.provider_id
    assert memory["credential_id"] == account.credential_id
    assert _capture(service, session, account, loan) is not None
    assert downloaded == []


@pytest.mark.parametrize("field", [
    "connected", "provider_id", "credential_id", "_epoch", "_closed",
])
def test_session_property_override_cannot_run_network_during_scope_capture(
    connected_core, monkeypatch, field,
):
    service, session, account, loan, _state, downloaded = connected_core
    calls = []
    def dynamic_value(_self):
        calls.append(field)
        raise RuntimeError("getter would have issued private network call")
    monkeypatch.setattr(type(session), field, property(dynamic_value), raising=False)
    assert _inert_session_state(session) is None
    assert _capture(service, session, account, loan) is None
    assert calls == []
    assert downloaded == []


def test_session_getattribute_proxy_cannot_override_memory_scope(
    connected_core, monkeypatch,
):
    service, session, account, loan, _state, downloaded = connected_core
    calls = []
    def dynamic(self, name):
        calls.append(name)
        raise RuntimeError("provider proxy must not be called")
    monkeypatch.setattr(type(session), "__getattribute__", dynamic)
    assert _inert_session_state(session) is None
    assert _capture(service, session, account, loan) is None
    assert calls == []
    assert downloaded == []


def test_lease_revoked_by_memory_only_disconnect_without_provider_getter(
    connected_core,
):
    service, session, account, loan, _state, downloaded = connected_core
    lease = _capture(service, session, account, loan)
    assert lease is not None
    session.__dict__["connected"] = False
    assert lease.current(service) is False
    assert downloaded == []


def test_lease_revoked_if_getter_installed_after_initial_capture(
    connected_core, monkeypatch,
):
    service, session, account, loan, _state, downloaded = connected_core
    lease = _capture(service, session, account, loan)
    assert lease is not None
    invoked = []
    def connected_getter(_self):
        invoked.append("called")
        return True
    monkeypatch.setattr(
        type(session), "connected", property(connected_getter), raising=False,
    )
    assert not lease.current(service)
    assert invoked == []
    assert downloaded == []
