"""P1 #6002 — closed session markers invalidate Home loan availability.

The view must not invoke provider properties or re-connect on refresh.
Positive cached loans stay descriptive, never current remaining capacity.
"""
from __future__ import annotations

import pytest

from cifereca.core.digital_loan_availability import digital_loan_availability
from tests.test_core_p1_6002_digital_loan_availability import (
    account, active_loan, configured, runtime, spec,
)


def make_state():
    borrower = account("demo", "card", "reader")
    state = runtime(
        [borrower], [configured("demo", "card")], [spec("demo")],
        [active_loan(borrower, "observed")],
    )
    return state, state._sessions[("demo", "card")]


def test_closed_session_does_not_reuse_stale_connected_flag():
    state, session = make_state()
    session._closed = False
    current, = digital_loan_availability(state, "alice")
    assert current.reason == "inventory_unverified"
    assert current.observed_loans == 1
    assert current.personal_ceiling == 4
    assert current.remaining is None

    # The upstream may revoke/close without first clearing connected=True.
    session._closed = True
    stale, = digital_loan_availability(state, "alice")
    assert session.connected is True
    assert stale.reason == "account_disconnected"
    assert stale.observed_loans == 1  # historical positive count is not erased
    assert stale.personal_ceiling == 4
    assert stale.status == "unknown"
    assert stale.remaining is None
    assert stale.eligibility == "not_verified"


def test_closure_property_is_not_invoked_during_home_read():
    state, _session = make_state()

    class UntrustedSession:
        provider_id = "demo"
        credential_id = "card"
        connected = True

        @property
        def _closed(self):
            pytest.fail("Home must not invoke a provider/session getter")

    state._sessions[("demo", "card")] = UntrustedSession()
    row, = digital_loan_availability(state, "alice")
    assert row.reason == "account_disconnected"
    assert row.remaining is None
