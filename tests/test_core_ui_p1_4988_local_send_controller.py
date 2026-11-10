"""#4988: GTK headless local-send follow-up requires a second consent.

No provider I/O or implicit A-to-B copy, even after a confirmed loan.
"""
from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from cifereca.core.borrow_for_identity_placement_resolution import (
    LenderLocalPlacement,
)
from cifereca.gui.cross_profile_local_send_controller import (
    offer_cross_profile_local_send,
)
from tests.test_core_ui_p1_4987_cross_profile_borrow_controller import (
    setup, Dialog,
)
from tests.test_core_p1_4662_cross_profile_borrow_preview import witness


def prepared(*, present=True):
    h = setup()
    w = witness("alice")
    local = LenderLocalPlacement(
        "present" if present else "unavailable",
        "lender_exact_local_placement_present" if present
        else "lender_delivery_unverified",
        _placement_id="placement-a" if present else None,
        loan="confirmed",
    )
    lookup = Mock(return_value=local)
    native = Mock(return_value={
        "status": "complete", "loan": "confirmed",
        "delivery": "present", "transfer": "completed",
        "reason_code": "independent_local_copy_completed",
        "retry_borrow": False,
    })
    h.service.resolve_cross_profile_lender_placement = lookup
    h.service.send_confirmed_lender_local_copy = native
    display = lambda: offer_cross_profile_local_send(
        h.window, borrowing_account=w.account,
        exact_notice=w.notice, expected_item=h.item,
        Adw=NS(MessageDialog=Dialog),
        TaskPriority=NS(USER_ACTION=1),
    )
    return h, w, lookup, native, display


def test_confirmed_placement_requires_distinct_send_confirmation():
    h, w, lookup, native, display = prepared()
    assert display() is True
    assert display() is False
    assert native.call_count == 0 and Dialog.made == []
    _, work, completed, _fail, _kw = h.window.runner.jobs[0]
    result = work()
    lookup.assert_called_once_with(
        requesting_identity_id="bob", borrowing_account=w.account,
        exact_notice=w.notice,
    )
    assert native.call_count == 0
    completed(result)
    assert len(Dialog.made) == 1
    assert "prêt et les droits fournisseur restent" in Dialog.made[0].description
    assert "send" in Dialog.made[0].responses
    Dialog.made[0].respond("send")
    assert len(h.window.runner.jobs) == 2
    assert native.call_count == 0
    _, send_work, done, _failed, _kw = h.window.runner.jobs[1]
    assert send_work()["status"] == "complete"
    native.assert_called_once_with(
        requesting_identity_id="bob", borrowing_account=w.account,
        exact_notice=w.notice, confirmed=True,
    )
    done(native.return_value)
    assert h.window._cross_profile_send_pending is None
    assert h.logs and h.logs[-1][0] == "info"


def test_no_local_placement_never_opens_consent_or_sends():
    h, _, lookup, native, display = prepared(present=False)
    assert display()
    _, work, done, _failed, _kw = h.window.runner.jobs[0]
    done(work())
    lookup.assert_called_once()
    assert Dialog.made == []
    native.assert_not_called()
    assert h.window._cross_profile_send_pending is None


def test_cancel_send_after_positive_placement_never_copies():
    h, _, _, native, display = prepared()
    assert display()
    _, work, done, _, _ = h.window.runner.jobs[0]
    done(work())
    Dialog.made[0].respond("cancel")
    assert len(h.window.runner.jobs) == 1
    assert h.window._cross_profile_send_pending is None
    native.assert_not_called()


def test_double_dialog_callback_schedules_one_native_copy():
    h, _, _, native, display = prepared()
    assert display()
    _, work, done, _, _ = h.window.runner.jobs[0]
    done(work())
    dialog = Dialog.made[0]
    dialog.respond("send")
    dialog.respond("send")
    dialog.respond("cancel")
    assert len(h.window.runner.jobs) == 2
    h.window.runner.jobs[1][1]()
    native.assert_called_once()


@pytest.mark.parametrize("rebind", ["reader", "source", "selection", "detail"])
def test_stale_ui_after_placement_check_never_shows_send_dialog(rebind):
    h, _, _, native, display = prepared()
    assert display()
    _, work, done, _, _ = h.window.runner.jobs[0]
    result = work()
    if rebind == "reader":
        h.service.current_identity = NS(identity_id="bob")
    elif rebind == "source":
        h.service._source_context_epoch += 1
    elif rebind == "selection":
        h.window._selected_search_id = "another"
    else:
        h.window.search_page.detail._rendered_search_detail = (
            replace(h.item), object(),
        )
    done(result)
    assert Dialog.made == []
    native.assert_not_called()
    assert h.window._cross_profile_send_pending is None


def test_revoked_send_token_before_worker_never_copies():
    h, _, _, native, display = prepared()
    assert display()
    _, work, done, _, _ = h.window.runner.jobs[0]
    done(work())
    Dialog.made[0].respond("send")
    h.window._cross_profile_send_pending = None
    assert h.window.runner.jobs[1][1]()["status"] == "blocked"
    native.assert_not_called()


def test_failed_native_copy_preserves_loan_message_without_private_details():
    h, _, _, native, display = prepared()
    native.return_value = {
        "status": "blocked", "loan": "confirmed", "delivery": "present",
        "transfer": "blocked", "reason_code": "/private/SECRET",
        "retry_borrow": False,
    }
    assert display()
    _, work, done, _, _ = h.window.runner.jobs[0]
    done(work())
    Dialog.made[0].respond("send")
    _, send_work, send_done, _, _ = h.window.runner.jobs[1]
    send_done(send_work())
    assert h.logs[-1][0] == "error"
    assert "prêt reste" in h.logs[-1][1].lower()
    assert "SECRET" not in repr(h.logs)
