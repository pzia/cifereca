"""#4987 integrated UI->Core consent->durable lender claim, no real HTTP/GI.

This is intentionally on a validation-only stacked branch (#5154 + #5158).
It must not enable the standalone UI in the production search flow.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

from cifereca.core.borrow_action_scope import borrow_claim_key
from cifereca.gui.cross_profile_borrow_controller import (
    present_cross_profile_lender_choice,
)
from tests.test_core_p1_4662_live_lender_preview import harness
from tests.test_core_p1_4987_cross_profile_executor import setup_borrower
from tests.test_core_ui_p1_4987_cross_profile_borrow_controller import (
    Dialog, Runner,
)


def setup(harness, monkeypatch):
    import cifereca.core.borrow_for_identity_live as live

    # Fresh synthetic provider evidence has timestamps around 1_000.
    # Neither the GUI nor Core may silently reinterpret expired facts.
    monkeypatch.setattr(live, "time", lambda: 1100)
    service, account, session, row, _old = setup_borrower(harness)
    assert service.current_identity.identity_id == "default"
    Dialog.made.clear()
    shown, messages = [], []
    item = row.notice
    panel = NS(
        _rendered_search_detail=(item, object()),
        show_cross_profile_borrow_progress_detail=lambda p, **kw:
            shown.append((p, kw)) or True,
    )
    window = NS(
        service=service, search_page=NS(detail=panel),
        _closed=False, _cross_profile_borrow_pending=None,
        _identity_context_generation=4, _account_context_generation=6,
        _selected_search_id="same-notice",
        _search_detail_request_generation=8,
        runner=Runner(),
        _activity_result=lambda msg: messages.append(("info", msg)),
        show_error=lambda msg, title: messages.append(("error", msg, title)),
    )

    def present():
        return present_cross_profile_lender_choice(
            window, expected_item=item, expected_edition_id="edition-1",
            observations=(row,),
            Adw=NS(MessageDialog=Dialog),
            TaskPriority=NS(USER_ACTION=1),
        )

    return service, account, session, item, window, shown, messages, present


def test_real_core_gtk_two_stage_consent_confirms_only_lenders_claim(
    harness, monkeypatch,
):
    service, account, session, item, window, shown, messages, present = setup(
        harness, monkeypatch,
    )
    requester = service.current_identity
    assert present() is True
    assert session.actions == 0
    assert service.storage.metadata_get(borrow_claim_key(account, item)) is None
    Dialog.made[0].respond("choice-0")
    assert len(Dialog.made) == 2
    assert session.actions == 0
    assert len(window.runner.jobs) == 0
    Dialog.made[1].respond("borrow")
    assert len(window.runner.jobs) == 1
    assert session.actions == 0
    _title, work, done, _failed, _kw = window.runner.jobs[0]
    result = work()
    assert result["status"] == "confirmed", result
    assert result["provider_verified"] is True
    assert result["transfer"] == "blocked"
    assert session.actions == 1
    assert service.current_identity is requester
    assert service.storage.loan_borrow_state(
        borrow_claim_key(account, item),
    )["status"] == "confirmed"
    done(result)
    assert len(shown) == 1
    progress, args = shown[0]
    assert (progress.loan, progress.delivery, progress.transfer) == (
        "confirmed", "unavailable", "blocked",
    )
    assert args["expected_item"] is item
    assert window._cross_profile_borrow_pending is None
    assert len(messages) == 1
    assert "transfert" in messages[0][1]
    # The supplier loan belongs only to the lender; a second user gesture
    # cannot reborrow the same lender+edition from an old observation.
    assert present() is False
    assert session.actions == 1


def test_real_core_rebind_after_menu_prevents_new_lender_claim(
    harness, monkeypatch,
):
    service, account, session, item, window, shown, messages, present = setup(
        harness, monkeypatch,
    )
    assert present() is True
    requester = service.current_identity
    # Same profile ID but another object: A->A rebind without epoch change.
    from dataclasses import replace
    service.current_identity = replace(requester)
    assert service.current_identity.identity_id == requester.identity_id
    Dialog.made[0].respond("choice-0")
    assert len(Dialog.made) == 1
    assert window.runner.jobs == []
    assert session.actions == 0
    assert service.storage.metadata_get(borrow_claim_key(account, item)) is None
    assert shown == [] and messages == []
