"""#4988: completed supplier loan presents local send only as a second action."""
from unittest.mock import Mock

from cifereca.core.borrow_for_identity_placement_resolution import (
    LenderLocalPlacement,
)
from tests.test_core_ui_p1_4987_composed_gtk_executor import setup
from tests.test_core_p1_4662_live_lender_preview import harness
from tests.test_core_ui_p1_4987_cross_profile_borrow_controller import Dialog


def _confirmed(harness, monkeypatch):
    service, account, session, item, window, shown, messages, present = setup(
        harness, monkeypatch,
    )
    assert present()
    Dialog.made[0].respond("choice-0")
    Dialog.made[1].respond("borrow")
    assert len(window.runner.jobs) == 1
    _, work, completed, _failed, _kw = window.runner.jobs[0]
    result = work()
    assert result["status"] == "confirmed"
    assert session.actions == 1
    completed(result)
    return service, account, session, item, window, shown, messages


def test_loan_confirmed_but_not_delivered_only_checks_placement_without_copy(
    harness, monkeypatch,
):
    service, account, session, item, window, shown, messages = _confirmed(
        harness, monkeypatch,
    )
    assert len(window.runner.jobs) == 2
    assert len(Dialog.made) == 2
    _, check, done, _failed, _kw = window.runner.jobs[1]
    resolution = check()
    assert resolution.status == "unavailable"
    done(resolution)
    assert len(Dialog.made) == 2
    assert session.actions == 1
    assert window._cross_profile_send_pending is None
    assert len(messages) == 1
    assert "non vérifiée" in messages[0][1]


def test_real_lender_loan_and_local_placement_trigger_second_send_consent(
    harness, monkeypatch,
):
    # The native send's successful receipt is synthetic; supplier POST/claim
    # is the real fixture, and no supplier call runs on the new send path.
    service, account, session, item, window, shown, messages = _confirmed(
        harness, monkeypatch,
    )
    # Post-loan local availability can change independently (e.g. sync).
    native = Mock(return_value={
        "status": "complete", "loan": "confirmed",
        "delivery": "present", "transfer": "completed",
        "reason_code": "independent_local_copy_completed",
        "retry_borrow": False,
    })
    monkeypatch.setattr(
        service, "resolve_cross_profile_lender_placement",
        lambda **_kw: LenderLocalPlacement(
            "present", "lender_exact_local_placement_present",
            _placement_id="placement-a", loan="confirmed",
        ),
    )
    monkeypatch.setattr(service, "send_confirmed_lender_local_copy", native)
    assert len(window.runner.jobs) == 2
    _, check, done, _failed, _kw = window.runner.jobs[1]
    assert native.call_count == 0
    done(check())
    assert len(Dialog.made) == 3
    assert native.call_count == 0
    Dialog.made[2].respond("send")
    assert len(window.runner.jobs) == 3
    assert native.call_count == 0
    _title, send_work, send_done, _failed, _kw = window.runner.jobs[2]
    sent = send_work()
    assert sent["status"] == "complete"
    native.assert_called_once_with(
        requesting_identity_id="default",
        borrowing_account=account, exact_notice=item, confirmed=True,
    )
    send_done(sent)
    assert session.actions == 1
    assert window._cross_profile_send_pending is None
    assert len(messages) == 2
    assert "Copie indépendante" in messages[-1][1]
