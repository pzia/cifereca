"""#4984: execute a confirmed withdrawal, not merely preview it."""
from dataclasses import replace
import os

import pytest

from cifereca.core import device_usb_removal_execute as executor
from tests.test_p1_2854_usb_removal_review import completed_copy


def test_confirmed_owned_usb_copy_is_physically_removed(completed_copy, monkeypatch):
    args, active, final, source = completed_copy
    monkeypatch.setattr(executor, "observe_linux_usb_mount", lambda **_: active["mount"])
    result = executor.execute_usb_removal(
        storage=args["storage"], copy_journal=args["journal"],
        removal_journal=args["journal"], lock_root=final.parent.parent.parent / "locks",
        mountpoint=args["mountpoint"], consent=args["consent"],
        current_profile=args["current_profile"],
    )
    assert result == executor.UsbRemovalExecution("removed", "usb_removal_committed")
    assert not final.exists()
    assert source.exists()
    assert args["journal"].get(args["consent"].copy_operation_id).phase == "completed"
    assert args["journal"].get(args["consent"].operation_id).phase == "completed"
    # Replaying the old confirmation must not delete anything else.
    assert executor.execute_usb_removal(
        storage=args["storage"], copy_journal=args["journal"],
        removal_journal=args["journal"], lock_root=final.parent.parent.parent / "locks",
        mountpoint=args["mountpoint"], consent=args["consent"],
        current_profile=args["current_profile"],
    ).status == "blocked"


def test_stale_copy_inode_cannot_be_removed(completed_copy, monkeypatch):
    args, active, final, source = completed_copy
    monkeypatch.setattr(executor, "observe_linux_usb_mount", lambda **_: active["mount"])
    original = final.with_suffix(".saved")
    final.rename(original)
    final.write_bytes(source.read_bytes())
    result = executor.execute_usb_removal(
        storage=args["storage"], copy_journal=args["journal"],
        removal_journal=args["journal"], lock_root=final.parent.parent.parent / "locks",
        mountpoint=args["mountpoint"], consent=args["consent"],
        current_profile=args["current_profile"],
    )
    assert result.status == "blocked"
    assert original.is_file() and final.is_file()
    assert args["journal"].get(args["consent"].operation_id) is None


def test_revoked_remove_intent_never_unlinks(completed_copy, monkeypatch):
    args, active, final, _ = completed_copy
    monkeypatch.setattr(executor, "observe_linux_usb_mount", lambda **_: active["mount"])
    consent = args["consent"]
    args["storage"].device_usb_intent_set(
        identity_id=consent.identity_id, device_id=consent.device_id,
        artifact_id=consent.artifact_id, desired_state="keep",
    )
    result = executor.execute_usb_removal(
        storage=args["storage"], copy_journal=args["journal"],
        removal_journal=args["journal"], lock_root=final.parent.parent.parent / "locks",
        mountpoint=args["mountpoint"], consent=consent,
        current_profile=args["current_profile"],
    )
    assert result.status == "blocked"
    assert final.exists()
    assert args["journal"].get(consent.operation_id) is None


def test_failed_durable_completion_stays_pending_without_false_success(
    completed_copy, monkeypatch,
):
    args, active, final, _ = completed_copy
    monkeypatch.setattr(executor, "observe_linux_usb_mount", lambda **_: active["mount"])
    journal = args["journal"]
    monkeypatch.setattr(journal, "compare_and_set", lambda *_: False)
    result = executor.execute_usb_removal(
        storage=args["storage"], copy_journal=journal, removal_journal=journal,
        lock_root=final.parent.parent.parent / "locks", mountpoint=args["mountpoint"],
        consent=args["consent"], current_profile=args["current_profile"],
    )
    assert result.status == "recovery_required"
    assert not final.exists()
    assert journal.get(args["consent"].operation_id).phase == "pending"
