"""#6937: lossless compact witness contract; no retirement or quota refund."""
from pathlib import Path
import sqlite3

import pytest

from cifereca.core import derived_variant_compact_receipt as compact
from cifereca.core import derived_variant_journal as journal
from cifereca.storage import Storage
from tests.test_core_p0_4520_derived_journal import _prepared_publication, _publish_reserved


@pytest.fixture
def committed(tmp_path):
    service, intent, reservation = _prepared_publication(tmp_path)
    db = service.storage
    try:
        _publish_reserved(db, intent, reservation)
        key = journal._COMMITTED + reservation.receipt_sha256
        yield db, intent, key, db.metadata_get(key)
    finally:
        db.close()


def test_lossless_reconstruction_after_reopen_and_independent_accounting(committed):
    db, intent, key, raw = committed
    before = journal.summarize_derived_journal(db)
    proof_key, proof = compact.encode_committed_witness(key, raw)
    assert len(proof) < len(raw)
    assert compact.decode_committed_witness(proof_key, proof) == (key, raw)
    assert compact.review_compact_capacity(db)["count"] == 0
    # Fixture-only insertion: no such writer is exposed or wired by Core.
    db.metadata_set(proof_key, proof)
    other = Storage(db.path)
    try:
        assert compact.decode_committed_witness(proof_key, other.metadata_get(proof_key)) == (key, raw)
        report = compact.review_compact_capacity(other)
        assert report["count"] == 1 and report["payload_bytes"] == len(proof)
        assert not any(report[k] for k in ("retention_authorized", "quota_refund_authorized",
                                           "destructive_cleanup_allowed"))
        assert key not in repr(report) and intent.parent_path not in repr(report)
        assert journal.summarize_derived_journal(other) == before
        assert other.metadata_get(key) == raw
        assert Path(intent.staged_path).samefile(intent.candidate_path)
    finally:
        other.close()


@pytest.mark.parametrize("bad", ["pending", "noncanonical", "wrong_digest", "oversize",
                                "nonce", "field", "version", "truncated", "bad_json"])
def test_corruption_never_changes_historical_receipt(committed, bad):
    db, intent, key, raw = committed
    proof_key, proof = compact.encode_committed_witness(key, raw)
    import json
    body = json.loads(proof)
    with pytest.raises(journal.DerivedJournalConflict):
        if bad == "pending":
            compact.encode_committed_witness(journal._slot(intent), raw)
        elif bad == "noncanonical":
            noncanonical = raw + " "
            compact.encode_committed_witness(journal._COMMITTED + journal._receipt_digest(noncanonical), noncanonical)
        elif bad == "wrong_digest":
            compact.decode_committed_witness(compact._PREFIX + "0" * 64, proof)
        elif bad == "oversize":
            compact.decode_committed_witness(proof_key, "x" * (compact._MAX_PROOF_BYTES + 1))
        elif bad == "bad_json":
            compact.decode_committed_witness(proof_key, "{")
        else:
            if bad == "nonce":
                body[1] = "0" * 32
            elif bad == "field":
                body[2][0] = "another-profile"
            elif bad == "version":
                body[0] = "derived.compact.v2"
            else:
                body[2].pop()
            compact.decode_committed_witness(proof_key, journal._encode(body))
    assert db.metadata_get(key) == raw
    assert compact.review_compact_capacity(db)["count"] == 0


@pytest.mark.parametrize("limit", ["count", "bytes", "row", "invalid"])
def test_separate_overflow_refuses_without_eviction(committed, monkeypatch, limit):
    db, intent, key, raw = committed
    proof_key, proof = compact.encode_committed_witness(key, raw)
    before = journal.summarize_derived_journal(db)
    db.metadata_set(proof_key, proof)
    if limit == "count":
        monkeypatch.setattr(compact, "_MAX_PROOFS", 1)
        original = __import__("json").loads(raw)
        original["nonce"] = "f" * 32
        second = journal._encode(original)
        another_key, another = compact.encode_committed_witness(
            journal._COMMITTED + journal._receipt_digest(second), second,
        )
        db.metadata_set(another_key, another)
    elif limit == "bytes":
        monkeypatch.setattr(compact, "_MAX_PROOF_TOTAL", len(proof) - 1)
    elif limit == "row":
        monkeypatch.setattr(compact, "_MAX_PROOF_BYTES", len(proof) - 1)
    else:
        db.metadata_set(compact._PREFIX + "0" * 64, proof)
    with pytest.raises(journal.DerivedJournalConflict):
        compact.review_compact_capacity(db)
    assert db.metadata_get(proof_key) == proof and db.metadata_get(key) == raw
    assert journal.summarize_derived_journal(db) == before


def test_nested_snapshot_refuses_without_committing_or_rolling_back(committed):
    db, intent, key, raw = committed
    sql = db._connect()
    other = sqlite3.connect(db.path)
    try:
        sql.execute("BEGIN IMMEDIATE")
        sql.execute("INSERT INTO metadata(key,value) VALUES('synthetic.compact.caller','pending')")
        with pytest.raises(journal.DerivedJournalConflict, match="nested_transaction"):
            compact.review_compact_capacity(db)
        assert sql.in_transaction
        assert sql.execute("SELECT value FROM metadata WHERE key='synthetic.compact.caller'").fetchone() == ("pending",)
        assert other.execute("SELECT value FROM metadata WHERE key='synthetic.compact.caller'").fetchone() is None
        sql.rollback()
        assert db.metadata_get(key) == raw
    finally:
        if sql.in_transaction:
            sql.rollback()
        other.close()


@pytest.mark.parametrize("case", ["paired", "orphan", "changed_original", "corrupt_shadow", "over_budget"])
def test_compact_shadow_cannot_refund_active_journal_admission(committed, monkeypatch, case):
    from dataclasses import replace
    db, intent, key, raw = committed
    proof_key, proof = compact.encode_committed_witness(key, raw)
    db.metadata_set(proof_key, proof if case != "corrupt_shadow" else "unreadable")
    if case == "orphan":
        with db._lock, db._connect() as sql:
            sql.execute("DELETE FROM metadata WHERE key=?", (key,))
    elif case == "changed_original":
        db.metadata_set(key, raw + " ")
    elif case == "over_budget":
        monkeypatch.setattr(compact, "_MAX_PROOF_TOTAL", len(proof) - 1)
    before = (db.metadata_get(key), db.metadata_get(proof_key))
    next_intent = replace(
        intent, options_fingerprint="another",
        staged_path=str(Path(intent.staged_path).with_name("next.epub")),
        candidate_path=str(Path(intent.candidate_path).with_name("next.epub")),
    )
    monkeypatch.setattr(journal, "_MAX_RECORDS", 2)
    monkeypatch.setattr(journal, "_MAX_RESERVED_OUTPUT_BYTES", 18)
    if case == "paired":
        new = journal.reserve_derived_publication(db, next_intent)
        assert db.metadata_get(new.key)
        report = journal.summarize_derived_journal(db)
        assert report["count"] == 2 and report["reserved_output_bytes"] == 18
    else:
        with pytest.raises(journal.DerivedJournalConflict, match="derived_compact_"):
            journal.reserve_derived_publication(db, next_intent)
        assert db.metadata_get(journal._slot(next_intent)) is None
    assert (db.metadata_get(key), db.metadata_get(proof_key)) == before
    assert Path(intent.staged_path).samefile(intent.candidate_path)
