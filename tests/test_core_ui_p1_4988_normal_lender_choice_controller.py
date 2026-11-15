"""#4988: true GTK choice -> confirmation -> single normal lender worker.

No GI required; the controller is exercised with real DTOs and fake dialogs.
Supplier eligibility is unknown before clicking; no provider GET/POST occurs
on render, choice, cancel or stale callbacks.
"""
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from cifereca.core.borrow_for_identity_discovery import PossibleBorrowLender
from cifereca.gui.cross_profile_normal_borrow_controller import (
    present_normal_cross_profile_lender_choice as present,
)
from tests.test_core_p1_4662_cross_profile_borrow_preview import witness
from tests.test_core_ui_p1_4987_cross_profile_borrow_controller import Dialog, Runner


GOOD = {
    "status": "confirmed", "provider_verified": True,
    "reason_code": "lender_loan_confirmed_transfer_unverified",
    "delivery": "unavailable", "transfer": "blocked",
    "retry_borrow": False,
}


def setup(*, multiple=False):
    Dialog.made.clear()
    first = witness("alice")
    item = first.notice
    candidate = PossibleBorrowLender(
        label="Alice", lender_identity_id="alice",
        _account=first.account, _notice=item,
    )
    candidates = (candidate,)
    if multiple:
        another = witness("charlie", credential=item.source_credential,
                          account="borrower-C")
        # The normal Core discovery would not return this under a single
        # provider/credential registry collision, but the GTK selection
        # controller must still handle any 0/1/N independently scoped tuple.
        other = PossibleBorrowLender(
            label="Charlie", lender_identity_id="charlie",
            _account=another.account, _notice=item,
        )
        candidates += (other,)
    identity = NS(identity_id="bob")
    calls, logs, shown = [], [], []
    reader = lambda **kwargs: (
        calls.append(("read", kwargs)) or candidates
    )
    attempts = []

    def exec_borrow(**kwargs):
        attempts.append(kwargs)
        return GOOD.copy()

    service = NS(
        current_identity=identity, _account_context_epoch=4,
        _source_context_epoch=9,
        possible_borrow_lenders_for_current_identity=reader,
        execute_normal_lender_borrow_for_current_identity=exec_borrow,
    )
    panel = NS(
        _rendered_search_detail=(item, object()),
        show_cross_profile_borrow_progress_detail=lambda progress, **kwargs:
            shown.append((progress, kwargs)) or True,
    )
    window = NS(
        service=service, search_page=NS(detail=panel),
        _closed=False, _cross_profile_borrow_pending=None,
        _identity_context_generation=2, _account_context_generation=3,
        _selected_search_id="selected", _search_detail_request_generation=8,
        runner=Runner(), _activity_result=lambda m: logs.append(("info", m)),
        show_error=lambda m, t: logs.append(("error", m, t)),
    )

    def show():
        return present(
            window, expected_item=item, Adw=NS(MessageDialog=Dialog),
            TaskPriority=NS(USER_ACTION=1),
        )

    return NS(window=window, service=service, item=item, candidates=candidates,
              identity=identity, attempts=attempts, logs=logs,
              shown=shown, calls=calls, show=show)


def test_choose_then_confirm_then_one_worker_under_exact_captured_B():
    h = setup(multiple=True)
    assert h.show() is True
    chooser = Dialog.made[0]
    assert {"cancel", "choice-0", "choice-1"} <= set(chooser.responses)
    assert "quota fournisseur non vérifiés" in chooser.description
    assert h.attempts == [] and h.window.runner.jobs == []
    chooser.respond("choice-1")
    assert len(Dialog.made) == 2 and h.attempts == []
    confirm = Dialog.made[1]
    assert "fournisseur peut le refuser" in confirm.description
    assert "confirmation distincte" in confirm.description
    confirm.respond("borrow")
    assert len(h.window.runner.jobs) == 1
    assert h.attempts == []
    _title, work, done, _failed, meta = h.window.runner.jobs[0]
    assert meta["kind"] == "borrow"
    result = work()
    assert result == GOOD
    assert len(h.attempts) == 1
    kw = h.attempts[0]
    assert kw["captured_candidate"] is h.candidates[1]
    assert kw["exact_notice"] is h.item
    assert kw["expected_requesting_identity_id"] == "bob"
    assert kw["expected_requesting_identity"] is h.identity
    assert kw["expected_context_epoch"] == 4
    assert kw["expected_source_epoch"] == 9
    assert kw["confirmed"] is True
    done(result)
    assert len(h.shown) == 1
    assert (h.shown[0][0].loan, h.shown[0][0].transfer) == (
        "confirmed", "blocked",
    )
    assert len(h.logs) == 1 and "copie séparés" in h.logs[0][1]
    assert h.window._cross_profile_borrow_pending is None


@pytest.mark.parametrize("stage", ["choice", "confirmation"])
def test_cancellation_does_not_schedule_any_borrow(stage):
    h = setup()
    assert h.show()
    if stage == "confirmation":
        Dialog.made[0].respond("choice-0")
    Dialog.made[-1].respond("cancel")
    assert h.attempts == [] and h.window.runner.jobs == []
    assert h.window._cross_profile_borrow_pending is None


@pytest.mark.parametrize("change", [
    "identity", "account_epoch", "source_epoch",
    "ui_identity_epoch", "ui_account_epoch", "selection",
    "detail", "detail_epoch", "service_rebind",
])
def test_stale_choice_never_leads_to_confirm_or_queued_post(change):
    h = setup()
    assert h.show()
    if change == "identity":
        h.service.current_identity = NS(identity_id="bob")
    elif change == "account_epoch":
        h.service._account_context_epoch += 1
    elif change == "source_epoch":
        h.service._source_context_epoch += 1
    elif change == "ui_identity_epoch":
        h.window._identity_context_generation += 1
    elif change == "ui_account_epoch":
        h.window._account_context_generation += 1
    elif change == "selection":
        h.window._selected_search_id = "another"
    elif change == "detail":
        h.window.search_page.detail._rendered_search_detail = (
            replace(h.item), object(),
        )
    elif change == "detail_epoch":
        h.window._search_detail_request_generation += 1
    else:
        h.window.service = NS(**vars(h.service))
    Dialog.made[0].respond("choice-0")
    assert len(Dialog.made) == 1
    assert h.window.runner.jobs == []
    assert h.attempts == []


def test_repeated_click_and_duplicate_confirmation_cannot_queue_two_workers():
    h = setup()
    assert h.show()
    assert not h.show()
    Dialog.made[0].respond("choice-0")
    Dialog.made[0].respond("choice-0")
    assert len(Dialog.made) == 2
    Dialog.made[1].respond("borrow")
    Dialog.made[1].respond("borrow")
    assert len(h.window.runner.jobs) == 1
    assert h.attempts == []


def test_worker_revalidates_scope_before_mutator_and_does_not_read_gtk_detail():
    h = setup()
    assert h.show()
    Dialog.made[0].respond("choice-0")
    Dialog.made[1].respond("borrow")
    job = h.window.runner.jobs[0]
    h.window.search_page.detail = NS(
        _rendered_search_detail=property(
            lambda _self: (_ for _ in ()).throw(
                AssertionError("GTK access from worker")
            )
        ),
    )
    # worker_current does not dereference panel/widget properties
    assert job[1]() == GOOD
    assert len(h.attempts) == 1
    h.window._selected_search_id = "stale"
    assert job[1]()["status"] == "blocked"
    assert len(h.attempts) == 1


def test_uncertain_result_exposes_no_supplier_exception_or_retry():
    h = setup()
    h.service.execute_normal_lender_borrow_for_current_identity = (
        lambda **kw: {
            "status": "verification_unavailable",
            "reason_code": "https://private.example/token=SECRET",
        }
    )
    assert h.show()
    Dialog.made[0].respond("choice-0")
    Dialog.made[1].respond("borrow")
    job = h.window.runner.jobs[0]
    job[2](job[1]())
    assert len(h.logs) == 1
    assert h.logs[0][0] == "error"
    assert "SECRET" not in repr(h.logs)
    assert "incertain" in h.logs[0][1]


@pytest.mark.parametrize("invalid", [
    {"delivery": "available"}, {"retry_borrow": True},
    {"provider_verified": 1}, {"reason_code": "wrong"},
    {"transfer": "completed"},
])
def test_forged_positive_callback_never_claims_success(invalid):
    h = setup()
    h.service.execute_normal_lender_borrow_for_current_identity = (
        lambda **_kw: {**GOOD, **invalid}
    )
    assert h.show()
    Dialog.made[0].respond("choice-0")
    Dialog.made[1].respond("borrow")
    job = h.window.runner.jobs[0]
    job[2](job[1]())
    assert h.shown == []
    assert h.logs[0][0] == "error"


def test_no_local_lender_or_no_mutator_does_not_open_gtk_choice():
    h = setup()
    h.service.possible_borrow_lenders_for_current_identity = lambda **_kw: ()
    assert h.show() is False and Dialog.made == []
    h = setup()
    delattr(h.service, "execute_normal_lender_borrow_for_current_identity")
    assert h.show() is False and Dialog.made == []
