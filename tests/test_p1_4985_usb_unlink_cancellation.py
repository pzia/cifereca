"""P1 #4985: user-selection cancellation at Core's irreversible USB unlink.

Real copied bytes, physical inode review and durable SQLite journal from the
existing completed-copy fixture. Only block/USB mount attestation is synthetic.
A cancellation that occurs *after* the worker's initial check must never unlink.
"""
from __future__ import annotations

import pytest

from cifereca.core import device_usb_removal_execute as removal
from cifereca.core import device_usb_sync_engine as engine
from cifereca.core.device_usb_dry_run import UsbDryRun
from cifereca.core.device_usb_removal_review import UsbRemovalReview
from tests.test_p1_2854_usb_removal_review import completed_copy
from tests.test_p1_2854_usb_live_source import live_args
from tests.test_p1_2854_usb_copy_preparation import copy_review
from tests.test_p1_4984_usb_sync_engine import _arguments, _row


@pytest.fixture
def args_for_unlink(completed_copy, monkeypatch):
    args, active, final, source = completed_copy
    monkeypatch.setattr(removal, "observe_linux_usb_mount", lambda **_: active["mount"])
    call = dict(
        storage=args["storage"], copy_journal=args["journal"],
        removal_journal=args["journal"], lock_root=final.parent.parent.parent / "locks",
        mountpoint=args["mountpoint"], consent=args["consent"],
        current_profile=args["current_profile"],
    )
    return call, final, source, args["journal"]


def test_cancelled_during_second_physical_review_does_not_reserve_or_unlink(
    args_for_unlink, monkeypatch,
):
    call, final, source, journal = args_for_unlink
    payload = final.read_bytes()
    original = removal.review_usb_removal
    state = {"reviews": 0, "cancelled": False, "checks": 0}

    def review(**kwargs):
        result = original(**kwargs)
        if result.status == "review":
            state["reviews"] += 1
            if state["reviews"] == 2:
                state["cancelled"] = True
        return result

    def check():
        state["checks"] += 1
        if state["cancelled"]:
            raise RuntimeError("selection_changed")

    monkeypatch.setattr(removal, "review_usb_removal", review)
    result = removal.execute_usb_removal(**call, check_cancelled=check)
    assert state["reviews"] == 2
    assert state["checks"] == 1
    assert result.status == "blocked"
    assert final.read_bytes() == source.read_bytes() == payload
    assert journal.get(call["consent"].operation_id) is None


def test_cancelled_after_durable_reservation_keeps_file_and_pending_recovery(
    args_for_unlink, monkeypatch,
):
    call, final, source, journal = args_for_unlink
    payload = final.read_bytes()
    state = {"reserved": False, "checks": 0}
    original_begin = journal.begin

    def begin(entry):
        accepted = original_begin(entry)
        if accepted:
            state["reserved"] = True
        return accepted

    def check():
        state["checks"] += 1
        if state["reserved"]:
            raise RuntimeError("selection_changed_after_reservation")

    monkeypatch.setattr(journal, "begin", begin)
    result = removal.execute_usb_removal(**call, check_cancelled=check)
    assert state["reserved"] is True and state["checks"] == 2
    assert result.status == "recovery_required"
    assert final.read_bytes() == source.read_bytes() == payload
    assert journal.get(call["consent"].operation_id).phase == "pending"
    # The reserved operation is one-shot: cancellation must not trigger an
    # automatic retry able to delete the same or a foreign replacement file.
    assert removal.execute_usb_removal(**call).status == "blocked"
    assert final.read_bytes() == payload


def test_stable_selection_still_unlinks_exact_managed_file(
    args_for_unlink,
):
    call, final, source, journal = args_for_unlink
    payload = final.read_bytes()
    checked = []
    result = removal.execute_usb_removal(
        **call, check_cancelled=lambda: checked.append(True),
    )
    assert result.status == "removed"
    assert len(checked) == 2
    assert not final.exists()
    assert source.read_bytes() == payload
    assert journal.get(call["consent"].operation_id).phase == "completed"


def test_invalid_cancel_callback_fails_before_any_file_or_journal_access(
    args_for_unlink,
):
    call, final, source, journal = args_for_unlink
    payload = final.read_bytes()
    assert removal.execute_usb_removal(
        **call, check_cancelled=object(),
    ).status == "blocked"
    assert final.read_bytes() == source.read_bytes() == payload
    assert journal.get(call["consent"].operation_id) is None


def test_sync_engine_forwards_one_user_revocation_token_to_locked_removal(
    args_for_unlink, tmp_path, monkeypatch,
):
    call, _, _, _ = args_for_unlink
    consent = call["consent"]
    data = _arguments(tmp_path, consent, None)
    data["rights"] = {}
    data["artifact_id"] = consent.artifact_id
    token_calls = []
    def check():
        token_calls.append("checked")

    data["check_cancelled"] = check
    monkeypatch.setattr(engine, "inspect_usb_dry_run", lambda **_: UsbDryRun(
        consent.identity_id, consent.device_id, "scanned", True, 1,
        (_row(consent.artifact_id, "remove_candidate"),),
    ))
    monkeypatch.setattr(engine, "SqliteUsbOperationJournal", lambda _: object())
    passed = []
    def supervised(**kw):
        passed.append(kw)
        assert kw["check_cancelled"] is check
        kw["check_cancelled"]()
        return removal.UsbRemovalExecution("removed", "usb_removal_committed")
    monkeypatch.setattr(engine, "execute_usb_removal", supervised)
    monkeypatch.setattr(
        engine, "record_verified_usb_observation",
        lambda **_: type("Report", (), {"status": "recorded"})(),
    )
    result = engine.synchronize_usb_reader(**data)
    assert result.status == "completed" and result.removed == 1
    assert len(passed) == 1
    # The additional post-removal check prevents a revoked GTK selection
    # from persisting a new observed presence in SQLite.
    assert token_calls == ["checked", "checked", "checked"]
