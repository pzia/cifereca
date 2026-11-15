"""#4985: read-only attribution of the *exact* completed USB copy operation."""
from __future__ import annotations

from dataclasses import replace
import sqlite3

import pytest

from cifereca.core import device_usb_operation_journal as journal_module
from cifereca.core.device_usb_removal_execute import UsbRemovalJournalEntry
from cifereca.core.device_usb_removal_review import UsbRemovalConsent
from tests.test_p1_2854_usb_copy_supervisor import copy
from tests.test_p1_4984_usb_sqlite_operation_journal import (
    _Storage, _finished_usb_copy,
)


def operations(storage, consent):
    return journal_module.completed_usb_managed_copy_operations(
        storage=storage, identity_id=consent.identity_id,
        device_id=consent.device_id, fingerprint=consent.usb_fingerprint,
    )


def test_fresh_install_does_not_create_table_for_read_only_attribution(tmp_path):
    store = _Storage(tmp_path / "new.sqlite3")
    assert journal_module.completed_usb_managed_copy_operations(
        storage=store, identity_id="alice",
        device_id="reader-a", fingerprint="uuid-reader-a",
    ) == {}
    with store._connect() as db:
        assert db.execute(
            "SELECT COUNT(*) FROM sqlite_master "
            "WHERE type='table' AND name='device_usb_operations'"
        ).fetchone() == (0,)


def test_exact_finished_copy_is_attributed_without_touching_file(copy, tmp_path):
    args, managed, journal, store, finished = _finished_usb_copy(copy, tmp_path)
    consent = args["consent"]
    file = managed / (consent.sha256 + ".epub")
    before = file.read_bytes()
    assert operations(store, consent) == {
        consent.artifact_id: consent.operation_id,
    }
    assert file.read_bytes() == before
    assert journal.get(consent.operation_id).phase == "completed"
    assert operations(store, replace(consent, identity_id="bob")) == {}
    assert operations(store, replace(consent, device_id="other-reader")) == {}
    assert operations(store, replace(consent, usb_fingerprint="other-volume")) == {}


def test_history_is_not_a_physical_presence_claim(copy, tmp_path):
    args, managed, journal, store, finished = _finished_usb_copy(copy, tmp_path)
    consent = args["consent"]
    path = managed / (consent.sha256 + ".epub")
    path.rename(path.with_suffix(".missing"))
    # Historical ID can still be displayed as "previous managed copy",
    # but the independent executor must deny deletion of a missing file.
    assert operations(store, consent) == {
        consent.artifact_id: consent.operation_id,
    }


def test_pending_and_completed_removal_both_revoke_copy_attribution(copy, tmp_path):
    args, _managed, journal, store, finished = _finished_usb_copy(copy, tmp_path)
    copy_consent = args["consent"]
    removal = UsbRemovalConsent(
        copy_consent.identity_id, copy_consent.device_id,
        copy_consent.usb_fingerprint, copy_consent.artifact_id,
        copy_consent.sha256, copy_consent.target_relative_path,
        copy_consent.operation_id, "f" * 32,
        copy_consent.confirmed_at, copy_consent.expires_at,
    )
    pending = UsbRemovalJournalEntry(removal)
    assert operations(store, copy_consent) == {
        copy_consent.artifact_id: copy_consent.operation_id,
    }
    assert journal.begin(pending)
    assert operations(store, copy_consent) == {}
    assert journal.compare_and_set(pending, replace(pending, phase="completed"))
    assert operations(store, copy_consent) == {}


def test_duplicate_finished_copy_never_selects_by_order(copy, tmp_path):
    args, _managed, journal, store, finished = _finished_usb_copy(copy, tmp_path)
    consent = args["consent"]
    original = journal.get(consent.operation_id)
    second = "d" * 32
    duplicate = replace(
        original,
        consent=replace(original.consent, operation_id=second),
        pin=replace(original.pin, staged_name=".cifereca-usb-" + second + ".part"),
        phase="pending",
    )
    assert journal.begin(duplicate)
    assert journal.compare_and_set(duplicate, replace(duplicate, phase="completed"))
    assert operations(store, consent) == {}


def test_corrupt_payload_or_over_budget_is_not_attributed(copy, tmp_path, monkeypatch):
    args, _managed, journal, store, finished = _finished_usb_copy(copy, tmp_path)
    consent = args["consent"]
    monkeypatch.setattr(journal_module, "_MAX_PREVIEW_OPERATIONS", 0)
    assert operations(store, consent) == {}
    monkeypatch.undo()
    with store._lock, store._connect() as db:
        db.execute(
            "UPDATE device_usb_operations SET payload='invalid-json' "
            "WHERE operation_id=?", (consent.operation_id,),
        )
        db.commit()
    assert operations(store, consent) == {}


def test_changed_second_read_fails_closed(copy, tmp_path, monkeypatch):
    args, _managed, journal, store, finished = _finished_usb_copy(copy, tmp_path)
    consent = args["consent"]
    original = journal_module.completed_usb_managed_receipts
    calls = []
    def stale(**kwargs):
        calls.append(1)
        result = original(**kwargs)
        return result if len(calls) == 1 else {}
    monkeypatch.setattr(journal_module, "completed_usb_managed_receipts", stale)
    assert operations(store, consent) == {}
    assert len(calls) == 2
