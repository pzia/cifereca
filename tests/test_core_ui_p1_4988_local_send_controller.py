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
    assert len(Dialog.made) == 1
    assert "retry" in Dialog.made[0].responses
    assert "send" not in Dialog.made[0].responses
    native.assert_not_called()
    Dialog.made[0].respond("close")
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


@pytest.mark.parametrize("rebind", ["reader", "source", "selection", "detail", "search_page"])
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
    elif rebind == "search_page":
        h.window.search_page = NS(detail=h.window.search_page.detail)
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



def test_confirmed_loan_missing_delivery_has_manual_read_only_retry():
    h, _, lookup, native, display = prepared(present=False)
    assert display() is True
    _, work, done, _, _ = h.window.runner.jobs[0]
    done(work())
    assert len(Dialog.made) == 1
    dialog = Dialog.made[0]
    assert "encore disponible" in dialog.title
    assert "retry" in dialog.responses
    assert len(h.window.runner.jobs) == 1
    assert "nouvel emprunt" in dialog.description
    native.assert_not_called()

    # Delivery reaches A after its one-shot supplier claim. This is a
    # *second local read*, never a provider POST nor a local copy yet.
    lookup.return_value = LenderLocalPlacement(
        "present", "lender_exact_local_placement_present",
        _placement_id="placement-a", loan="confirmed",
    )
    dialog.respond("retry")
    dialog.respond("retry")
    assert len(h.window.runner.jobs) == 2
    assert lookup.call_count == 1
    assert native.call_count == 0
    _, work2, done2, _, _ = h.window.runner.jobs[1]
    done2(work2())
    assert lookup.call_count == 2
    assert len(Dialog.made) == 2
    assert "send" in Dialog.made[1].responses
    native.assert_not_called()
    Dialog.made[1].respond("send")
    _, send_work, send_done, _, _ = h.window.runner.jobs[2]
    send_done(send_work())
    assert native.call_count == 1
    assert h.window._cross_profile_send_pending is None


def test_missing_delivery_dialog_close_releases_without_reborrow_or_send():
    h, _, lookup, native, display = prepared(present=False)
    assert display()
    _, work, done, _, _ = h.window.runner.jobs[0]
    done(work())
    Dialog.made[0].respond("close")
    Dialog.made[0].respond("retry")
    assert len(h.window.runner.jobs) == 1
    lookup.assert_called_once()
    native.assert_not_called()
    assert h.window._cross_profile_send_pending is None


def test_missing_delivery_recheck_after_profile_change_is_revoked():
    h, _, lookup, native, display = prepared(present=False)
    assert display()
    _, work, done, _, _ = h.window.runner.jobs[0]
    done(work())
    h.service._account_context_epoch += 1
    Dialog.made[0].respond("retry")
    assert len(h.window.runner.jobs) == 1
    lookup.assert_called_once()
    native.assert_not_called()
    assert h.window._cross_profile_send_pending is None


def test_uncertain_loan_never_offers_local_recheck():
    h, _, lookup, native, display = prepared(present=False)
    lookup.return_value = LenderLocalPlacement(
        "unavailable", "lender_loan_not_confirmed", loan="unknown",
    )
    assert display()
    _, work, done, _, _ = h.window.runner.jobs[0]
    done(work())
    assert Dialog.made == []
    assert h.window._cross_profile_send_pending is None
    native.assert_not_called()



class _GtkDetailOffWorker:
    """Every GTK detail read outside the main-thread callback is forbidden."""

    @property
    def detail(self):
        raise AssertionError("worker touched GTK DetailPanel")


def test_local_placement_worker_never_reads_gtk_detail():
    h, _, lookup, native, display = prepared()
    assert display()
    _, work, completed, _failed, _kwargs = h.window.runner.jobs[0]
    original_page = h.window.search_page
    h.window.search_page = _GtkDetailOffWorker()
    result = work()
    lookup.assert_called_once()
    native.assert_not_called()
    h.window.search_page = original_page
    completed(result)
    assert len(Dialog.made) == 1
    assert "send" in Dialog.made[0].responses


def test_local_send_worker_never_reads_gtk_detail():
    h, _, lookup, native, display = prepared()
    assert display()
    _, check, ready, _failed, _kwargs = h.window.runner.jobs[0]
    ready(check())
    Dialog.made[0].respond("send")
    _, work, completed, _failed, _kwargs = h.window.runner.jobs[1]
    original_page = h.window.search_page
    h.window.search_page = _GtkDetailOffWorker()
    assert work()["status"] == "complete"
    native.assert_called_once()
    h.window.search_page = original_page
    completed(native.return_value)
    assert h.logs[-1][0] == "info"


@pytest.mark.parametrize("kind", ["source", "identity", "selection", "detail_generation"])
def test_local_send_worker_rejects_scalar_context_rebound_before_copy(kind):
    h, _, lookup, native, display = prepared()
    assert display()
    _, check, ready, _failed, _kwargs = h.window.runner.jobs[0]
    ready(check())
    Dialog.made[0].respond("send")
    _, work, _completed, _failed, _kwargs = h.window.runner.jobs[1]
    if kind == "source":
        h.service._source_context_epoch += 1
    elif kind == "identity":
        h.service.current_identity = NS(identity_id="bob")
    elif kind == "selection":
        h.window._selected_search_id = "other"
    else:
        h.window._search_detail_request_generation += 1
    assert work()["status"] == "blocked"
    native.assert_not_called()
