"""P1 #4987: two simultaneous clicks share one lender-side durable claim."""
from __future__ import annotations

from threading import Event, Thread

from cifereca.core.borrow_action_scope import borrow_claim_key
from tests.test_core_p1_4662_live_lender_preview import harness
from tests.test_core_p1_4987_cross_profile_executor import setup_borrower, execute


def test_second_click_while_lender_provider_running_never_dispatches(harness):
    service, account, session, row, preview = setup_borrower(harness)
    started = Event()
    release = Event()
    calls = []
    original = session.borrow_explicit_with_claim

    def pause_after_atomic_claim(*args, **kwargs):
        calls.append("entered-provider")
        assert service.storage.loan_borrow_state(
            borrow_claim_key(account, row.notice),
        )["status"] == "request_sent"
        started.set()
        assert release.wait(10), "synthetic provider release not signalled"
        return original(*args, **kwargs)

    session.borrow_explicit_with_claim = pause_after_atomic_claim
    first = []

    def invoke():
        first.append(execute(service, row, preview))

    task = Thread(target=invoke, daemon=True)
    task.start()
    try:
        assert started.wait(10), "first synthetic borrow did not reach provider"
        # The first mutation has reserved the entire lender+edition key.
        # A second click cannot pass preview or claim admission while it
        # is still pending, and cannot issue another upstream request.
        second = execute(service, row, preview)
        assert second["provider_verified"] is False
        assert second["status"] in ("blocked", "verification_unavailable")
        assert second["retry_borrow"] is False
        assert calls == ["entered-provider"]
    finally:
        release.set()
        task.join(timeout=10)
    assert not task.is_alive()
    assert len(first) == 1 and first[0]["status"] == "confirmed"
    assert calls == ["entered-provider"]
    assert session.actions == 1
    assert service.storage.loan_borrow_state(
        borrow_claim_key(account, row.notice),
    )["status"] == "confirmed"
