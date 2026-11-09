"""P1 #4984: production USB operation journal stores immutable typed receipts."""
from __future__ import annotations

from dataclasses import replace
import sqlite3
import threading

from cifereca.core.device_usb_operation_journal import SqliteUsbOperationJournal
from cifereca.core.device_usb_removal_execute import UsbRemovalJournalEntry
from cifereca.core.device_usb_removal_review import UsbRemovalConsent
from cifereca.core.device_usb_copy_supervisor import execute_usb_copy
from tests.test_p1_2854_usb_copy_supervisor import copy


class _Storage:
    def __init__(self, path):
        self.path = path
        self._lock = threading.RLock()

    def _connect(self):
        return sqlite3.connect(self.path)


def test_exact_usb_copy_stays_completed_after_reopening_sqlite(copy, tmp_path):
    args, managed, _, _ = copy
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
