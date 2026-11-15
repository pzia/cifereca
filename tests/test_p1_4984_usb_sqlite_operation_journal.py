"""P1 #4984: production USB operation journal stores immutable typed receipts."""
from __future__ import annotations

from dataclasses import replace
import sqlite3
import threading

from cifereca.core.device_usb_operation_journal import SqliteUsbOperationJournal, completed_usb_managed_receipts
from cifereca.core.device_usb_removal_execute import UsbRemovalJournalEntry
from cifereca.core.device_usb_removal_review import UsbRemovalConsent
from cifereca.core.device_usb_copy_supervisor import execute_usb_copy
from tests.test_p1_2854_usb_copy_supervisor import copy
from tests.test_p1_2854_usb_copy_preparation import copy_review


class _Storage:
    def __init__(self, path):
        self.path = path
        self._lock = threading.RLock()

    def _connect(self):
        return sqlite3.connect(self.path)


def test_exact_usb_copy_stays_completed_after_reopening_sqlite(copy, tmp_path):
    args, managed, _, resolved = copy
    from types import SimpleNamespace
    resolved.service = SimpleNamespace(write_usb_stage=lambda payload, append: append(payload))
    store = _Storage(tmp_path / "usb-operations.sqlite3")
    journal = SqliteUsbOperationJournal(store)
    assert journal.get(args["consent"].operation_id) is None
    result = execute_usb_copy(**(args | {"journal": journal}))
    assert result.status == "completed", result.reason
    another = SqliteUsbOperationJournal(store)
    final = another.get(args["consent"].operation_id)
    assert final is not None and final.phase == "completed"
    assert final.consent == args["consent"]
    assert final.rights == args["rights"]
    assert final.receipt == result.receipt
    assert final.pin is not None
    assert final.final_version is not None
    assert not another.begin(final)
    assert (managed / (args["consent"].sha256 + ".epub")).is_file()


def _remove_consent(operation_id):
    return UsbRemovalConsent(
        "alice", "reader-a", "uuid-reader-a",
        "sha256:" + "a" * 64, "a" * 64,
        "Books/" + "a" * 64 + ".epub",
        "b" * 32, operation_id, 1_800_000_000, 1_800_000_060,
    )


def test_exact_removal_phase_is_atomic_and_replay_is_rejected(tmp_path):
    journal = SqliteUsbOperationJournal(_Storage(tmp_path / "state.sqlite3"))
    first = UsbRemovalJournalEntry(_remove_consent("c" * 32))
    assert journal.begin(first)
    assert journal.get(first.consent.operation_id) == first
    assert not journal.begin(first)
    completed = replace(first, phase="completed")
    assert journal.compare_and_set(first, completed)
    assert journal.get(first.consent.operation_id) == completed
    assert not journal.compare_and_set(first, completed)
    assert not journal.begin(completed)


def test_parallel_pending_targets_do_not_share_a_destination(tmp_path):
    journal = SqliteUsbOperationJournal(_Storage(tmp_path / "state.sqlite3"))
    a = UsbRemovalJournalEntry(_remove_consent("c" * 32))
    b = UsbRemovalJournalEntry(_remove_consent("d" * 32))
    assert journal.begin(a)
    assert not journal.begin(b)
    assert journal.compare_and_set(a, replace(a, phase="completed"))
    assert journal.begin(b)


def test_operation_journal_uses_the_existing_canonical_storage_db(copy_review):
    storage = copy_review["storage"]
    journal = SqliteUsbOperationJournal(storage)
    consent = _remove_consent("e" * 32)
    entry = UsbRemovalJournalEntry(consent)
    assert journal.begin(entry)
    assert SqliteUsbOperationJournal(storage).get(consent.operation_id) == entry
    with storage._lock, storage._connect() as db:
        recorded = db.execute(
            "SELECT phase FROM device_usb_operations WHERE operation_id=?",
            (consent.operation_id,),
        ).fetchone()
    assert recorded == ("pending",)


def _finished_usb_copy(copy, tmp_path):
    from types import SimpleNamespace
    args, managed, _events, resolved = copy
    resolved.service = SimpleNamespace(write_usb_stage=lambda payload, append: append(payload))
    store = _Storage(tmp_path / "observed-journal.sqlite3")
    journal = SqliteUsbOperationJournal(store)
    finished = execute_usb_copy(**(args | {"journal": journal}))
    assert finished.status == "completed", finished.reason
    assert finished.receipt is not None
    return args, managed, journal, store, finished


def test_completed_receipt_requires_exact_identity_reader_fingerprint_and_disk_scan(
    copy, tmp_path,
):
    from cifereca.core.device_usb_preview import (
        UsbDeviceScan, UsbObservedFile, preview_usb_delta,
    )
    args, managed, journal, store, finished = _finished_usb_copy(copy, tmp_path)
    c = args["consent"]
    preview = lambda identity, device, fingerprint: completed_usb_managed_receipts(
        storage=store, identity_id=identity, device_id=device,
        fingerprint=fingerprint,
    )
    receipts = preview(c.identity_id, c.device_id, c.usb_fingerprint)
    assert receipts == {c.artifact_id: finished.receipt}
    for wrong in (
        ("bob", c.device_id, c.usb_fingerprint),
        (c.identity_id, "reader-b", c.usb_fingerprint),
        (c.identity_id, c.device_id, "other-volume"),
    ):
        assert preview(*wrong) == {}

    binding = {
        "identity_id": c.identity_id, "device_id": c.device_id,
        "enabled": True, "target_directory": "Books",
        "metadata": {"usb_fingerprint": c.usb_fingerprint},
    }
    placement = {
        "device_id": c.device_id, "artifact_id": c.artifact_id,
        "desired_state": "remove", "observed_state": "unknown",
        "target_relative_path": c.target_relative_path,
        "metadata": {"intent_identity_id": c.identity_id},
    }
    now = 1_800_000_000
    observed = UsbDeviceScan(
        c.identity_id, c.device_id, c.usb_fingerprint, now, True,
        {c.artifact_id: UsbObservedFile(c.target_relative_path, c.sha256, True)},
    )
    checked = preview_usb_delta(
        identity_id=c.identity_id, binding=binding, placements=[placement],
        local={}, scan=observed, receipts=receipts, now=now,
    )
    assert len(checked) == 1
    assert checked[0].action == "remove_candidate"
    assert checked[0].reason == "managed_removal_requires_confirmation"
    assert checked[0].executable is False
    assert checked[0].observed == "present"
    # The receipt is only history: without a fresh physical scan, no removal.
    old = preview_usb_delta(
        identity_id=c.identity_id, binding=binding, placements=[placement],
        local={}, scan=None, receipts=receipts, now=now,
    )
    assert old[0].action != "remove_candidate"
    assert old[0].observed == "unknown"
    target = managed / c.sha256.__add__(".epub")
    assert target.is_file()


def test_pending_removal_invalidates_original_receipt_without_unlink(copy, tmp_path):
    args, managed, journal, store, finished = _finished_usb_copy(copy, tmp_path)
    c = args["consent"]
    assert completed_usb_managed_receipts(
        storage=store, identity_id=c.identity_id,
        device_id=c.device_id, fingerprint=c.usb_fingerprint,
    ) == {c.artifact_id: finished.receipt}
    withdrawal = UsbRemovalConsent(
        c.identity_id, c.device_id, c.usb_fingerprint, c.artifact_id,
        c.sha256, c.target_relative_path,
        c.operation_id, "b" * 32, c.confirmed_at, c.expires_at,
    )
    started = UsbRemovalJournalEntry(withdrawal)
    assert journal.begin(started)
    assert completed_usb_managed_receipts(
        storage=store, identity_id=c.identity_id,
        device_id=c.device_id, fingerprint=c.usb_fingerprint,
    ) == {}
    assert (managed / (c.sha256 + ".epub")).is_file()
    assert journal.compare_and_set(started, replace(started, phase="completed"))
    assert completed_usb_managed_receipts(
        storage=store, identity_id=c.identity_id,
        device_id=c.device_id, fingerprint=c.usb_fingerprint,
    ) == {}


def test_uninitialized_corrupt_and_over_budget_journal_fail_closed(copy, tmp_path, monkeypatch):
    empty = _Storage(tmp_path / "never-used.sqlite3")
    assert completed_usb_managed_receipts(
        storage=empty, identity_id="alice", device_id="reader-a",
        fingerprint="uuid-reader-a",
    ) == {}
    with empty._connect() as db:
        assert db.execute(
            "SELECT COUNT(*) FROM sqlite_master "
            "WHERE type='table' AND name='device_usb_operations'"
        ).fetchone() == (0,)
    args, _managed, _journal, store, finished = _finished_usb_copy(copy, tmp_path)
    c = args["consent"]
    monkeypatch.setattr(
        __import__("cifereca.core.device_usb_operation_journal", fromlist=["_MAX_PREVIEW_OPERATIONS"]),
        "_MAX_PREVIEW_OPERATIONS", 0,
    )
    assert completed_usb_managed_receipts(
        storage=store, identity_id=c.identity_id,
        device_id=c.device_id, fingerprint=c.usb_fingerprint,
    ) == {}
    monkeypatch.undo()
    with store._lock, store._connect() as db:
        db.execute(
            "UPDATE device_usb_operations SET payload='not-json' "
            "WHERE operation_id=?", (c.operation_id,),
        )
        db.commit()
    assert completed_usb_managed_receipts(
        storage=store, identity_id=c.identity_id,
        device_id=c.device_id, fingerprint=c.usb_fingerprint,
    ) == {}


def test_two_completed_copies_of_same_artifact_never_pick_first(copy, tmp_path):
    args, managed, journal, store, finished = _finished_usb_copy(copy, tmp_path)
    c = args["consent"]
    original = journal.get(c.operation_id)
    duplicate_op = "d" * 32
    duplicated = replace(
        original,
        consent=replace(original.consent, operation_id=duplicate_op),
        pin=replace(original.pin, staged_name=".cifereca-usb-" + duplicate_op + ".part"),
        phase="pending",
    )
    assert journal.begin(duplicated)
    assert journal.compare_and_set(duplicated, replace(duplicated, phase="completed"))
    assert completed_usb_managed_receipts(
        storage=store, identity_id=c.identity_id, device_id=c.device_id,
        fingerprint=c.usb_fingerprint,
    ) == {}
    assert (managed / (c.sha256 + ".epub")).is_file()
