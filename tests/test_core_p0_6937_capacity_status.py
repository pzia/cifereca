"""#6937: capacity status observes both namespaces in one SQLite snapshot."""
import pytest

from cifereca.core import derived_variant_capacity_status as capacity
from cifereca.core import derived_variant_compact_shadow as shadow
from cifereca.core import derived_variant_journal as journal
from cifereca.core import derived_variant_compact_receipt as compact
from cifereca.storage import Storage
from tests.test_core_p0_6937_compact_shadow import committed


def test_capacity_status_has_no_authority_or_disk_claim(committed):
    db, intent, _placement, key, raw, proof_key, proof, args = committed
    expected = journal.summarize_derived_journal(db)
    plain = capacity.review_derived_capacity_status(db)
    assert plain["status"] == "observed"
    assert (plain["pending_receipts"], plain["committed_receipts"]) == (0, 1)
    assert plain["receipt_payload_bytes"] == expected["payload_bytes"]
    assert plain["declared_output_bytes"] == intent.output_size
    assert plain["paired_compact_shadows"] == 0
    assert plain["receipt_count_remaining"] == 1023
    assert not any(plain[key] for key in (
        "physical_disk_usage_measured", "full_filesystem_inventory",
        "new_transform_admitted", "quota_refund_authorized",
        "retention_authorized", "destructive_cleanup_allowed",
    ))
    shadow.write_compact_shadow(db, **args)
    observed = capacity.review_derived_capacity_status(db)
    assert observed["paired_compact_shadows"] == 1
    assert observed["compact_shadow_payload_bytes"] == len(proof.encode("ascii"))
    for field in ("receipt_count", "receipt_payload_bytes", "declared_output_bytes",
                  "receipt_count_remaining", "declared_output_remaining"):
        assert observed[field] == plain[field]
    other = Storage(db.path)
    try:
        assert capacity.review_derived_capacity_status(other) == observed
    finally:
        other.close()


@pytest.mark.parametrize("corruption", ["orphan", "wrong_compact", "wrong_active"])
def test_invalid_namespaces_are_not_free_capacity(committed, corruption):
    db, _intent, _placement, key, raw, proof_key, proof, args = committed
    shadow.write_compact_shadow(db, **args)
    if corruption == "orphan":
        with db._lock, db._connect() as sql:
            sql.execute("DELETE FROM metadata WHERE key=?", (key,))
    elif corruption == "wrong_compact":
        db.metadata_set(proof_key, "unreadable")
    else:
        db.metadata_set(key, "unreadable")
    with pytest.raises(journal.DerivedJournalConflict):
        capacity.review_derived_capacity_status(db)


def test_exact_capacity_boundary_and_unrelated_caller_transaction(committed, monkeypatch):
    db, _intent, _placement, _key, _raw, _proof_key, _proof, _args = committed
    monkeypatch.setattr(capacity, "_MAX_RECORDS", 1)
    result = capacity.review_derived_capacity_status(db)
    assert result["saturation_observed"]
    assert result["receipt_count_remaining"] == 0
    conn = db._connect()
    conn.execute("BEGIN IMMEDIATE")
    conn.execute("INSERT INTO metadata(key,value) VALUES('synthetic.caller','pending')")
    try:
        with pytest.raises(journal.DerivedJournalConflict, match="derived_capacity_nested_transaction"):
            capacity.review_derived_capacity_status(db)
        assert conn.in_transaction
        assert db._connect().execute("SELECT value FROM metadata WHERE key='synthetic.caller'").fetchone() == ("pending",)
    finally:
        conn.rollback()
    assert db.metadata_get("synthetic.caller") is None


def test_pending_reservation_is_counted_without_publication(tmp_path):
    from tests.test_core_p0_4520_recovery_operator import _setup

    service, intent, reservation = _setup(tmp_path)
    db = service.storage
    try:
        before = capacity.review_derived_capacity_status(db)
        assert before["pending_receipts"] == 1
        assert before["committed_receipts"] == 0
        assert before["receipt_count"] == 1
        assert before["declared_output_bytes"] == intent.output_size
        assert before["paired_compact_shadows"] == 0
        assert not before["new_transform_admitted"]
        assert db.metadata_get(reservation.key) is not None
        assert capacity.review_derived_capacity_status(db) == before
    finally:
        db.close()

def test_profile_diagnostic_is_not_a_second_quota(committed):
    db, intent, _placement, _key, _raw, _proof_key, _proof, _args = committed
    total = capacity.review_derived_capacity_status(db)
    same = capacity.review_derived_capacity_status(db, identity_id="id1")
    foreign = capacity.review_derived_capacity_status(db, identity_id="id2")
    assert same["profile_scoped"] and not total["profile_scoped"]
    assert total["profile_committed_receipts"] is None
    assert same["profile_committed_receipts"] == 1
    assert same["profile_pending_receipts"] == 0
    assert same["profile_declared_output_bytes"] == intent.output_size
    assert foreign["profile_committed_receipts"] == 0
    assert foreign["profile_declared_output_bytes"] == 0
    for field in ("receipt_count_remaining", "receipt_payload_remaining",
                  "declared_output_remaining", "declared_output_limit"):
        assert same[field] == foreign[field] == total[field]
    with pytest.raises(journal.DerivedJournalConflict, match="derived_capacity_invalid_profile"):
        capacity.review_derived_capacity_status(db, identity_id="foreign\\x00id")
