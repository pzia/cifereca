"""#4982 full-vector CAS on real persistent Notice C; no executor."""
from dataclasses import replace

import pytest

from cifereca.core.notice_c_aggregate_inputs import (
    capture_public_aggregate_inputs, check_public_aggregate_inputs_txn,
)
from cifereca.core.notice_c_cover_index import (
    persist_consolidated_cover_source, read_consolidated_notice_cover,
)
from cifereca.core.notice_cover_repository import evict_notice_cover
from cifereca.storage import Storage
from tests.test_core_p1_2385_notice_cover_persistence import (
    PUBLIC, PRIVATE, URL, OTHER, notice, store,
)


def capture(db, document):
    value = capture_public_aggregate_inputs(db, document)
    assert value is not None
    return value


def check(db, value):
    with db._lock, db._connect() as sql:
        sql.execute("BEGIN IMMEDIATE")
        return check_public_aggregate_inputs_txn(
            sql, value.canonical.document, expected_digest=value.input_digest,
        )


def publish_c(db, source, *, edition="edition:aggregate-cas"):
    return persist_consolidated_cover_source(
        db, canonical_edition_id=edition, source=source,
        relation_proof_id="fixture:strong:aggregate-cas",
    )


def test_complete_vector_changes_on_member_revision_and_watermark(tmp_path):
    path = tmp_path / "aggregate-input.sqlite"
    db = Storage(path)
    try:
        a = store(db, notice(observed_at=10))
        key_b = replace(PUBLIC, provider_id="seller-b", record_id="b")
        b = store(db, notice(key=key_b, observed_at=10))
        publish_c(db, a)
        c = publish_c(db, b)
        snapshot = capture(db, c.document)
        assert len(snapshot.sources) == 2
        assert snapshot.canonical == c and check(db, snapshot) == snapshot
        assert {s.document for s in snapshot.sources} == {a.document, b.document}
        assert store(db, notice(observed_at=11)) == a
        # Same N2 epoch, but a newer source watermark is relevant to an
        # in-flight publication even when image bytes have not changed.
        with pytest.raises(ValueError, match="inputs_changed"):
            check(db, snapshot)
        refreshed = capture(db, c.document)
        assert refreshed.canonical == c
        changed = store(db, notice(OTHER, key=key_b, observed_at=12))
        with pytest.raises(ValueError, match="inputs_changed"):
            check(db, refreshed)
        candidate = capture(db, c.document)
        assert changed in candidate.sources and b not in candidate.sources
        # Capture is not a relation reattestation or canonical publication.
        assert read_consolidated_notice_cover(db, c.document) == c
    finally:
        db.close()
    db = Storage(path)
    try:
        assert check(db, candidate) == candidate
        updated = publish_c(db, changed)
        assert updated.epoch > c.epoch
        with pytest.raises(ValueError, match="inputs_changed"):
            check(db, candidate)
        assert db.entitlements() == [] and db.acquisitions() == []
    finally:
        db.close()


def test_eviction_and_same_content_aba_refuse_old_aggregate_input(tmp_path):
    db = Storage(tmp_path / "aggregate-eviction.sqlite")
    try:
        a = store(db, notice(observed_at=10))
        c = publish_c(db, a)
        before = capture(db, c.document)
        assert evict_notice_cover(db, a.document, expected_epoch=a.epoch)
        assert capture_public_aggregate_inputs(db, c.document) is None
        with pytest.raises(ValueError, match="inputs_changed"):
            check(db, before)
        # Durable C stays readable, no fetch and no new rights.
        assert read_consolidated_notice_cover(db, c.document) == c
        revived = store(db, notice(observed_at=10), epoch=2)
        assert revived.epoch == 3
        after = capture(db, c.document)
        assert after.input_digest != before.input_digest
        assert after.sources == (revived,)
        with pytest.raises(ValueError, match="inputs_changed"):
            check(db, before)
    finally:
        db.close()


def test_private_or_missing_member_refuses_entire_public_vector(tmp_path):
    db = Storage(tmp_path / "aggregate-private.sqlite")
    try:
        a = store(db, notice(observed_at=10))
        c = publish_c(db, a)
        private = store(db, notice(key=PRIVATE, observed_at=10))
        private_c = publish_c(db, private)
        assert private_c.document == c.document
        assert capture_public_aggregate_inputs(db, c.document) is None
        assert read_consolidated_notice_cover(db, c.document) == private_c
        with db._lock, db._connect() as sql:
            assert not sql.in_transaction
            with pytest.raises(ValueError, match="transaction_required"):
                check_public_aggregate_inputs_txn(
                    sql, c.document, expected_digest="0" * 64,
                )
        assert db.entitlements() == [] and db.acquisitions() == []
    finally:
        db.close()
