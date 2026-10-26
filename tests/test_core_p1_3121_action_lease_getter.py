"""P1 #3121: no dynamic session.accounts getter at action-admission time.

All sessions, tokens and loans are offline BnF fixture objects; the tests
perform no remote request, ACSM staging, ADEPT or rights mutation.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from cifereca.core.loan_action_scope import (
    _inert_session_accounts, capture_loan_action_scope,
)
from tests.test_core_p0_2055_bnf_explicit_stage import connected_core


def _capture(service, session, account, loan):
    return capture_loan_action_scope(
        service, loan, account, session, expected_identity_id="default",
    )


def test_normal_base_accounts_allow_exact_bnf_scope_without_provider_getter(connected_core):
    service, session, account, loan, _state, downloaded = connected_core
    assert _inert_session_accounts(session) is session._accounts
    assert _capture(service, session, account, loan) is not None
    assert downloaded == []


def test_overridden_accounts_property_is_not_executed_to_authorize_action(
    connected_core, monkeypatch,
):
    service, session, account, loan, _state, downloaded = connected_core
    triggered = []

    def external_accounts(_self):
        triggered.append("provider getter invoked")
        raise RuntimeError("would have issued an authenticated provider request")

    monkeypatch.setattr(type(session), "accounts", property(external_accounts))
    assert _inert_session_accounts(session) is None
    assert _capture(service, session, account, loan) is None
    assert triggered == []
    assert downloaded == []


@pytest.mark.parametrize("variant", ["missing", "oversized", "copied", "duplicate"])
def test_non_unique_or_unknown_session_memory_refuses_without_source_io(
    connected_core, variant,
):
    service, session, account, loan, _state, downloaded = connected_core
    original = session._accounts
    try:
        if variant == "missing":
            del session.__dict__["_accounts"]
        elif variant == "oversized":
            session._accounts = [account] * 513
        elif variant == "copied":
            session._accounts = [replace(account)]
        else:
            session._accounts = [account, account]
        assert _capture(service, session, account, loan) is None
        assert downloaded == []
    finally:
        session._accounts = original


def test_bound_session_without_override_still_checks_scope_change(connected_core):
    service, session, account, loan, _state, downloaded = connected_core
    before = _capture(service, session, account, loan)
    assert before is not None and before.current(service)
    session._accounts = [replace(account)]
    assert not before.current(service)
    assert downloaded == []
