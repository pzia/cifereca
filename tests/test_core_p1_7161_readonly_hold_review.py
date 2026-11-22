"""Exact, read-only canonical ADEPT retry-hold review (#7161)."""
from dataclasses import replace
import sqlite3

import pytest
from cifereca.core import canonical_adept_attempt as marker
from cifereca.locking import InterProcessLock
from cifereca.storage import Storage
from cifereca.core.canonical_adept_attempt import (
    _key, _value, describe_loan_adept_attempt, pending_loan_adept_attempt,
)
from tests.test_core_p0_3171_atomic_publication_v14 import (
    new_source, register, store,
)


def _write(storage, source, value):
    with storage._lock, storage._connect() as db:
        db.execute("INSERT INTO metadata(key,value) VALUES (?,?)", (_key(source), value))


def test_legacy_marker_is_classified_without_reset_or_raw_operation(store):
    source, _ = new_source(kind="loan", branch="dilicom")
    register(store, "A", "seller", "card-A")
    assert describe_loan_adept_attempt(store, source, offer_id="loan-offer") == {
        "state": "none", "unlock_authorized": False,
    }
    marker = _value("loan-offer", "f" * 32)
    _write(store, source, marker)
    same = describe_loan_adept_attempt(store, source, offer_id="loan-offer")
    changed = describe_loan_adept_attempt(store, source, offer_id="new-offer")
    assert same == {"state": "held_same_offer", "unlock_authorized": False}
    assert changed == {"state": "held_other_offer", "unlock_authorized": False}
    assert pending_loan_adept_attempt(store, source)
    assert store.metadata_get(_key(source)) == marker
    assert "f" * 32 not in repr((same, changed))


def test_invalid_hold_still_blocks_and_does_not_grant_retry(store):
    source, _ = new_source(kind="loan", branch="dilicom")
    register(store, "A", "seller", "card-A")
    _write(store, source, "{not-json")
    assert describe_loan_adept_attempt(store, source, offer_id="x") == {
        "state": "held_invalid", "unlock_authorized": False,
    }
    assert pending_loan_adept_attempt(store, source)


def test_metadata_unreadable_fails_closed_without_exception():
    source, _ = new_source(kind="loan")

    class Unreadable:
        def metadata_get(self, _key):
            raise OSError("no access")

    assert describe_loan_adept_attempt(Unreadable(), source, offer_id="x") == {
        "state": "unreadable", "unlock_authorized": False,
    }
    assert pending_loan_adept_attempt(Unreadable(), source)
    assert describe_loan_adept_attempt(Unreadable(), source, offer_id="") == {
        "state": "invalid_offer", "unlock_authorized": False,
    }


def test_other_rights_and_invalid_sources_cannot_be_reinterpreted(store):
    purchase, _ = new_source(kind="purchase")
    assert describe_loan_adept_attempt(store, purchase, offer_id="x") == {
        "state": "invalid_source", "unlock_authorized": False,
    }


@pytest.mark.parametrize("bad", ["blob", "duplicate", "padded", "oversized", "empty"])
def test_v1_marker_with_ambiguous_storage_is_never_a_reviewed_claim(store, bad):
    source, _ = new_source(kind="loan", branch="dilicom")
    value = _value("offer", "f" * 32)
    if bad == "blob":
        value = sqlite3.Binary(value.encode("ascii"))
    elif bad == "duplicate":
        value = value[:-1] + ',"schema":1}'
    elif bad == "padded":
        value += " "
    elif bad == "oversized":
        value += " " * 8192
    else:
        value = ""
    _write(store, source, value)
    assert pending_loan_adept_attempt(store, source)
    assert describe_loan_adept_attempt(store, source, offer_id="offer") == {
        "state": "held_invalid", "unlock_authorized": False,
    }


def test_marker_operations_never_settle_caller_transaction(store, tmp_path, monkeypatch):
    source, _ = new_source(kind="loan", branch="dilicom")
    register(store, "A", "seller", "card-A")
    lock = tmp_path / "acquisition.lock"
    monkeypatch.setattr("cifereca.core.canonical_recovery.acquisition_lock_file", lambda: lock)
    db = store._connect()
    other = sqlite3.connect(store.path)
    try:
        with InterProcessLock(lock) as lease:
            operation = marker.begin_loan_adept_attempt(
                store, source=source, offer_id="offer", shared_lock=lease)
            before = db.execute("SELECT value FROM metadata WHERE key=?", (_key(source),)).fetchone()
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT INTO metadata(key,value) VALUES('synthetic.caller','pending')")
            assert pending_loan_adept_attempt(store, source)
            assert describe_loan_adept_attempt(store, source, offer_id="offer")["state"] == "unreadable"
            for action in (
                lambda: marker.begin_loan_adept_attempt(
                    store, source=source, offer_id="offer", shared_lock=lease),
                lambda: marker.release_unsubmitted_attempt(
                    store, source=source, offer_id="offer", operation_id=operation, shared_lock=lease),
            ):
                with pytest.raises(ValueError, match="nested_transaction"):
                    action()
                assert db.in_transaction
                assert db.execute("SELECT value FROM metadata WHERE key='synthetic.caller'").fetchone() == ("pending",)
                assert other.execute("SELECT value FROM metadata WHERE key='synthetic.caller'").fetchone() is None
                assert other.execute("SELECT value FROM metadata WHERE key=?", (_key(source),)).fetchone() == before
            db.rollback()
            assert pending_loan_adept_attempt(store, source)
    finally:
        if db.in_transaction:
            db.rollback()
        other.close()


def test_durable_marker_transaction_restores_sync_and_preserves_other_claim(store, tmp_path, monkeypatch):
    source, _ = new_source(kind="loan", branch="dilicom")
    register(store, "A", "seller", "card-A")
    sibling = replace(source, right_id="other-loan")
    lock = tmp_path / "acquisition.lock"
    monkeypatch.setattr("cifereca.core.canonical_recovery.acquisition_lock_file", lambda: lock)
    db = store._connect()
    previous = db.execute("PRAGMA synchronous").fetchone()[0]
    seen = []
    db.set_trace_callback(seen.append)
    try:
        with InterProcessLock(lock) as lease:
            first = marker.begin_loan_adept_attempt(store, source=source, offer_id="offer", shared_lock=lease)
            marker.begin_loan_adept_attempt(store, source=sibling, offer_id="other", shared_lock=lease)
            with pytest.raises(ValueError, match="changed"):
                marker.release_unsubmitted_attempt(
                    store, source=source, offer_id="different", operation_id=first, shared_lock=lease)
            assert db.execute("PRAGMA synchronous").fetchone() == (previous,)
            restored = Storage(store.path)
            try:
                assert pending_loan_adept_attempt(restored, source)
                assert pending_loan_adept_attempt(restored, sibling)
            finally:
                restored.close()
            marker.release_unsubmitted_attempt(store, source=source, offer_id="offer",
                                               operation_id=first, shared_lock=lease)
            assert not pending_loan_adept_attempt(store, source)
            assert pending_loan_adept_attempt(store, sibling)
            assert db.execute("PRAGMA synchronous").fetchone() == (previous,)
        assert sum("PRAGMA SYNCHRONOUS=FULL" in line.upper() for line in seen) == 4
    finally:
        db.set_trace_callback(None)


@pytest.mark.parametrize("change", ["operation", "offer", "deleted", "blob", "same_value"])
def test_reviewed_v1_marker_selects_exact_negative_fact_only(store, change):
    source, _ = new_source(kind="loan", branch="dilicom")
    raw = _value("private-offer", "f" * 32)
    _write(store, source, raw)
    sibling = replace(source, right_id="sibling")
    _write(store, sibling, raw)
    review = marker.review_loan_adept_marker(store, source)
    assert review.state == "held_phase_unknown"
    assert review.fingerprint and not review.unlock_authorized
    assert "f" * 32 not in repr(review) and review.fingerprint not in repr(review)
    db = store._connect()
    compare = marker.matches_reviewed_loan_adept_marker_in_transaction
    assert not compare(db, source=source, review=review)
    try:
        db.execute("BEGIN IMMEDIATE")
        assert compare(db, source=source, review=review)
        assert not compare(db, source=sibling, review=review)
        if change == "deleted":
            db.execute("DELETE FROM metadata WHERE key=?", (_key(source),))
        else:
            value = {
                "operation": _value("private-offer", "a" * 32),
                "offer": _value("rotated", "f" * 32),
                "blob": sqlite3.Binary(raw.encode("ascii")),
                "same_value": raw,
            }[change]
            db.execute("UPDATE metadata SET value=? WHERE key=?", (value, _key(source)))
        assert compare(db, source=source, review=review) is (change == "same_value")
        assert db.in_transaction
        assert db.execute("SELECT value FROM metadata WHERE key=?", (_key(sibling),)).fetchone() == (raw,)
    finally:
        db.rollback()
    assert store.metadata_get(_key(source)) == raw
    assert marker.review_loan_adept_marker(store, source) == review


def test_missing_invalid_or_unreadable_marker_never_supplies_review_fingerprint(store):
    source, _ = new_source(kind="loan")
    absent = marker.review_loan_adept_marker(store, source)
    assert absent.state == "none" and not absent.fingerprint
    _write(store, source, "{malformed")
    invalid = marker.review_loan_adept_marker(store, source)
    assert invalid.state == "held_invalid" and not invalid.fingerprint
    db = store._connect()
    try:
        db.execute("BEGIN")
        unreadable = marker.review_loan_adept_marker(store, source)
        assert unreadable.state == "unreadable" and not unreadable.fingerprint
        for review in (absent, invalid, unreadable):
            assert not review.unlock_authorized
            assert not marker.matches_reviewed_loan_adept_marker_in_transaction(
                db, source=source, review=review)
        assert db.in_transaction
    finally:
        db.rollback()


@pytest.mark.parametrize("reason", ["core_pre_dispatch_refusal", "runtime_attested_not_submitted"])
def test_exact_unsubmitted_witness_survives_interrupted_release(store, tmp_path, monkeypatch, reason):
    source, _ = new_source(kind="loan", branch="dilicom")
    register(store, "A", "seller", "card-A")
    sibling = replace(source, right_id="sibling")
    lock = tmp_path / "acquisition.lock"
    monkeypatch.setattr("cifereca.core.canonical_recovery.acquisition_lock_file", lambda: lock)
    with InterProcessLock(lock) as lease:
        operation = marker.begin_loan_adept_attempt(store, source=source, offer_id="offer", shared_lock=lease)
        assert marker.review_loan_adept_unsubmitted(store, source=source).state == "not_recorded"
        def interrupted(*args, **kwargs):
            raise OSError("synthetic crash after first commit")
        monkeypatch.setattr(marker, "_consume_proven_unsubmitted", interrupted)
        with pytest.raises(OSError):
            marker.release_proven_unsubmitted_attempt(
                store, source=source, offer_id="offer", operation_id=operation,
                shared_lock=lease, reason=reason)
        restored = Storage(store.path)
        try:
            fact = marker.review_loan_adept_unsubmitted(restored, source=source)
            assert fact.state == "non_submission_recorded" and fact.reason == reason
            assert fact.marker_fingerprint == marker.review_loan_adept_marker(restored, source).fingerprint
            assert not fact.unlock_authorized and fact.marker_fingerprint not in repr(fact)
            assert marker.pending_loan_adept_attempt(restored, source)
            assert marker.review_loan_adept_unsubmitted(restored, source=sibling).state == "not_recorded"
            with pytest.raises(ValueError, match="unsubmitted_pending"):
                marker.begin_loan_adept_attempt(restored, source=source, offer_id="new", shared_lock=lease)
            with pytest.raises(ValueError, match="unsubmitted_conflict"):
                marker.release_unsubmitted_attempt(restored, source=source, offer_id="offer",
                                                   operation_id=operation, shared_lock=lease)
            db = restored._connect()
            with restored._lock, db:
                db.execute("UPDATE metadata SET value=? WHERE key=?", (
                    marker._value("offer", "a" * 32), marker._key(source)))
            assert marker.review_loan_adept_unsubmitted(restored, source=source).state == "unverified"
            assert marker.pending_loan_adept_attempt(restored, source)
        finally:
            restored.close()


def test_live_success_consumes_proof_and_fence_together(store, tmp_path, monkeypatch):
    source, _ = new_source(kind="loan", branch="dilicom")
    register(store, "A", "seller", "card-A")
    lock = tmp_path / "acquisition.lock"
    monkeypatch.setattr("cifereca.core.canonical_recovery.acquisition_lock_file", lambda: lock)
    with InterProcessLock(lock) as lease:
        op = marker.begin_loan_adept_attempt(store, source=source, offer_id="offer", shared_lock=lease)
        with pytest.raises(ValueError, match="changed"):
            marker.release_proven_unsubmitted_attempt(store, source=source, offer_id="other",
                operation_id=op, shared_lock=lease, reason="core_pre_dispatch_refusal")
        assert marker.review_loan_adept_unsubmitted(store, source=source).state == "not_recorded"
        marker.release_proven_unsubmitted_attempt(store, source=source, offer_id="offer",
            operation_id=op, shared_lock=lease, reason="core_pre_dispatch_refusal")
        assert not marker.pending_loan_adept_attempt(store, source)
        assert marker.review_loan_adept_unsubmitted(store, source=source).state == "not_recorded"
        assert marker.begin_loan_adept_attempt(store, source=source, offer_id="new", shared_lock=lease) != op
