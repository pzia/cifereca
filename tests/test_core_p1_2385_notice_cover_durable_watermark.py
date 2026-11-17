"""#2385/#6168: partial C observations preserve both evidence and freshness."""
from __future__ import annotations

import json

import pytest

from cifereca.core.notice_source_observation import (
    NoticeSourceKey, admit_source_notice,
)
from cifereca.core.notice_cover_repository import (
    persist_notice_cover, read_notice_cover,
)
from cifereca.storage import Storage


KEY = NoticeSourceKey(
    provider_id="bnf", visibility="public",
    identity_id=None, credential_id=None, account_id=None,
    branch_id="catalogue", record_id="ark:watermark",
    edition_id="digital:1", manifestation_role="digital",
)
A = "https://covers.example.org/one.jpg"
B = "https://covers.example.org/two.jpg"


def publish(db, value, at, *, status="complete", complete=True, epoch=None):
    proof = admit_source_notice(
        key=KEY, parser_id="bnf-parser", parser_version="1",
        observed_at=at, status=status,
        raw_fields={"media": {"cover": value}},
    )
    return persist_notice_cover(
        db, proof, field_path=("media", "cover"),
        cover_inventory_complete=complete, expected_epoch=epoch,
    )


def stored_row(db):
    with db._lock, db._connect() as conn:
        value = conn.execute(
            "SELECT value FROM metadata WHERE key LIKE 'notice.c.observation.v1:%'"
        ).fetchone()[0]
    return json.loads(value)


def test_late_complete_cannot_override_retained_cover_after_newer_partial(tmp_path):
    path = tmp_path / "source.sqlite"
    db = Storage(path)
    try:
        original = publish(db, A, 10)
        partial = publish(db, B, 20, status="partial")
        assert partial == original
        assert stored_row(db)["last_seen_at"] == 20
        assert stored_row(db)["epoch"] == 1
    finally:
        db.close()
    # The monotone freshness watermark must survive a process restart,
    # without inventing a new N2 revision or discarding attested source A.
    reopened = Storage(path)
    try:
        assert read_notice_cover(reopened, original.document) == original
        with pytest.raises(ValueError, match="stale_source"):
            publish(reopened, B, 15)
        with pytest.raises(ValueError, match="stale_source"):
            publish(reopened, B, 19, complete=False)
        assert read_notice_cover(reopened, original.document) == original
        updated = publish(reopened, B, 21)
        assert updated.epoch == 2
        assert updated.attribute.candidates[0].url == B
        assert stored_row(reopened)["last_seen_at"] == 21
    finally:
        reopened.close()


def test_same_source_refresh_updates_seen_watermark_without_epoch_increment(tmp_path):
    db = Storage(tmp_path / "source.sqlite")
    try:
        original = publish(db, A, 1)
        assert publish(db, A, 30) == original
        assert stored_row(db)["epoch"] == 1
        assert stored_row(db)["last_seen_at"] == 30
        with pytest.raises(ValueError, match="stale_source"):
            publish(db, B, 25)
        assert publish(db, B, 31).epoch == 2
    finally:
        db.close()


def test_first_pending_and_complete_empty_keep_documentary_states(tmp_path):
    db = Storage(tmp_path / "source.sqlite")
    try:
        pending = publish(db, None, 1, complete=False)
        assert pending.attribute.state == "pending"
        complete = publish(db, None, 2)
        assert complete.attribute.state == "missing"
        assert complete.epoch == pending.epoch + 1
        retained = publish(db, B, 6, complete=False)
        assert retained == complete
        assert stored_row(db)["last_seen_at"] == 6
        with pytest.raises(ValueError, match="stale_source"):
            publish(db, B, 5)
        assert read_notice_cover(db, complete.document).attribute.state == "missing"
    finally:
        db.close()


def test_legacy_source_row_without_watermark_is_backward_compatible(tmp_path):
    db = Storage(tmp_path / "source.sqlite")
    try:
        original = publish(db, A, 10)
        with db._lock, db._connect() as conn:
            key, raw = conn.execute(
                "SELECT key,value FROM metadata WHERE key LIKE 'notice.c.observation.v1:%'"
            ).fetchone()
            record = json.loads(raw)
            del record["last_seen_at"]
            conn.execute(
                "UPDATE metadata SET value=? WHERE key=?",
                (json.dumps(record, sort_keys=True), key),
            )
        assert read_notice_cover(db, original.document) == original
        assert publish(db, B, 11, status="partial") == original
        assert stored_row(db)["last_seen_at"] == 11
    finally:
        db.close()


@pytest.mark.parametrize("bad", [-1, 0, "10", True, 2**70])
def test_tampered_watermark_fails_closed_without_inventing_missing(tmp_path, bad):
    db = Storage(tmp_path / "source.sqlite")
    try:
        original = publish(db, A, 10)
        with db._lock, db._connect() as conn:
            key, raw = conn.execute(
                "SELECT key,value FROM metadata WHERE key LIKE 'notice.c.observation.v1:%'"
            ).fetchone()
            row = json.loads(raw)
            row["last_seen_at"] = bad
            conn.execute(
                "UPDATE metadata SET value=? WHERE key=?",
                (json.dumps(row, sort_keys=True), key),
            )
        with pytest.raises(ValueError, match="notice_c_persisted_corrupt"):
            read_notice_cover(db, original.document)
        with pytest.raises(ValueError, match="notice_c_persisted_corrupt"):
            publish(db, B, 100)
    finally:
        db.close()



def test_source_fence_survives_eviction_restart_and_rejects_stale_cas(tmp_path):
    from cifereca.core.notice_cover_repository import _opaque_key, _fence_key
    path = tmp_path / "eviction.sqlite"
    db = Storage(path)
    try:
        a = publish(db, A, 10)
        b = publish(db, B, 11, epoch=a.epoch)
        assert publish(db, B, 30) == b
        assert b.epoch == 2
        key = _opaque_key(b.document)
        with db._lock, db._connect() as conn:
            conn.execute("DELETE FROM metadata WHERE key=?", (key,))
            marker = json.loads(conn.execute(
                "SELECT value FROM metadata WHERE key=?", (_fence_key(key),),
            ).fetchone()[0])
        assert set(marker) == {"epoch", "last_seen_at", "proof"}
        assert marker["epoch"] == 2 and marker["last_seen_at"] == 30
        assert "https" not in repr(marker)
        assert read_notice_cover(db, a.document) is None
    finally:
        db.close()
    db = Storage(path)
    try:
        from cifereca.core.notice_cover_repository import read_notice_cover_write_epoch
        with pytest.raises(ValueError, match="unqualified_eviction"):
            read_notice_cover_write_epoch(db, b.document)
        for stale_epoch in (0, a.epoch, b.epoch):
            with pytest.raises(ValueError, match="epoch_changed"):
                publish(db, A, 31, epoch=stale_epoch)
        with pytest.raises(ValueError, match="stale_source"):
            publish(db, A, 29)
        with pytest.raises(ValueError, match="stale_source"):
            publish(db, A, 30)
        assert read_notice_cover(db, b.document) is None
        # Exact proof rehydration in the same second is safe, but must
        # receive a NEW epoch, not revive the old worker's CAS.
        revived = publish(db, B, 30)
        assert revived.epoch == 3
        with pytest.raises(ValueError, match="epoch_changed"):
            publish(db, A, 31, epoch=b.epoch)
        assert publish(db, A, 31, epoch=revived.epoch).epoch == 4
        assert db.entitlements() == [] and db.acquisitions() == []
    finally:
        db.close()


def test_legacy_retained_row_lazily_seeds_fence_and_corruption_fails_closed(tmp_path):
    from cifereca.core.notice_cover_repository import _opaque_key, _fence_key
    db = Storage(tmp_path / "legacy-fence.sqlite")
    try:
        a = publish(db, A, 10)
        key = _opaque_key(a.document)
        fence_key = _fence_key(key)
        with db._lock, db._connect() as conn:
            conn.execute("DELETE FROM metadata WHERE key=?", (fence_key,))
        assert publish(db, A, 20) == a
        with db._lock, db._connect() as conn:
            assert json.loads(conn.execute(
                "SELECT value FROM metadata WHERE key=?", (fence_key,),
            ).fetchone()[0])["last_seen_at"] == 20
            conn.execute("UPDATE metadata SET value=? WHERE key=?",
                         ('{"epoch":true}', fence_key))
        with pytest.raises(ValueError, match="source_fence_corrupt"):
            publish(db, B, 21)
        assert read_notice_cover(db, a.document) == a
        assert stored_row(db)["last_seen_at"] == 20
    finally:
        db.close()



def test_transactional_eviction_fences_old_jobs_after_restart(tmp_path):
    from cifereca.core.notice_cover_repository import (
        evict_notice_cover, read_notice_cover_write_epoch,
        persist_notice_cover_many,
    )
    from cifereca.core.notice_c_cover_index import (
        persist_consolidated_cover_source, read_consolidated_notice_cover,
    )
    path = tmp_path / "eviction.sqlite"
    db = Storage(path)
    try:
        original = publish(db, A, 10, epoch=0)
        doc = original.document
        aggregate = persist_consolidated_cover_source(
            db, canonical_edition_id="edition:eviction-test", source=original,
            relation_proof_id="strong:fixture:eviction",
        )
        assert publish(db, B, 20, status="partial") == original
        assert read_notice_cover_write_epoch(db, doc) == 1
        with pytest.raises(ValueError, match="epoch_changed"):
            evict_notice_cover(db, doc, expected_epoch=0)
        assert evict_notice_cover(db, doc, expected_epoch=1)
        assert read_notice_cover(db, doc) is None
        assert read_notice_cover_write_epoch(db, doc) == 2
        assert not evict_notice_cover(db, doc, expected_epoch=2)
        assert read_consolidated_notice_cover(db, aggregate.document) == aggregate
        with db._lock, db._connect() as conn:
            fence = conn.execute(
                "SELECT value FROM metadata WHERE key LIKE 'notice.c.source-fence.v1:%'"
            ).fetchone()[0]
            marker = json.loads(fence)
            assert set(marker) == {"epoch", "last_seen_at", "proof", "evicted"}
            assert marker["epoch"] == 2 and marker["last_seen_at"] == 20
            assert marker["evicted"] is True
            assert A not in fence and B not in fence
    finally:
        db.close()

    db = Storage(path)
    try:
        assert read_notice_cover_write_epoch(db, doc) == 2
        for epoch in (0, 1):
            with pytest.raises(ValueError, match="epoch_changed"):
                publish(db, B, 30, epoch=epoch)
        with pytest.raises(ValueError, match="stale_source"):
            publish(db, B, 19)
        with pytest.raises(ValueError, match="stale_source"):
            publish(db, B, 20)
        # Same-second renewal requires the new write epoch, not an old job.
        fresh = publish(db, A, 20, epoch=2)
        assert fresh.epoch == 3 and fresh.document == doc
        assert fresh != original
        with pytest.raises(ValueError, match="epoch_changed"):
            publish(db, B, 21, epoch=1)
        with pytest.raises(ValueError, match="stale_canonical_cover_source"):
            persist_consolidated_cover_source(
                db, canonical_edition_id="edition:eviction-test", source=original,
                relation_proof_id="strong:fixture:eviction",
            )
        assert evict_notice_cover(db, doc, expected_epoch=3)
        delayed = admit_source_notice(
            key=KEY, parser_id="bnf-parser", parser_version="1",
            observed_at=19, status="complete", raw_fields={"media": {"cover": B}},
        )
        independent = admit_source_notice(
            key=KEY, parser_id="bnf-parser", parser_version="1",
            observed_at=21, status="complete", raw_fields={"media": {"cover": B}},
        )
        rejected, current = persist_notice_cover_many(
            db, [delayed, independent], field_path=("media", "cover"),
            cover_inventory_complete=True,
        )
        assert rejected is None and current.epoch == 5
        assert read_notice_cover(db, doc) == current
        assert read_consolidated_notice_cover(db, aggregate.document) == aggregate
        assert db.entitlements() == [] and db.acquisitions() == []
    finally:
        db.close()


def test_eviction_rollback_and_corrupt_fence_refuse_without_source_loss(tmp_path):
    from cifereca.core.notice_cover_repository import (
        evict_notice_cover, read_notice_cover_write_epoch,
    )
    db = Storage(tmp_path / "eviction-rollback.sqlite")
    try:
        original = publish(db, A, 10)
        with db._lock, db._connect() as conn:
            conn.execute(
                "CREATE TRIGGER fixture_evict_refuse BEFORE DELETE ON metadata "
                "WHEN OLD.key LIKE 'notice.c.observation.v1:%' "
                "BEGIN SELECT RAISE(ABORT, 'fixture_evict_refused'); END"
            )
        import sqlite3
        with pytest.raises(sqlite3.IntegrityError, match="fixture_evict_refused"):
            evict_notice_cover(db, original.document, expected_epoch=1)
        assert read_notice_cover(db, original.document) == original
        assert read_notice_cover_write_epoch(db, original.document) == 1
        with db._lock, db._connect() as conn:
            assert conn.execute(
                "SELECT COUNT(*) FROM metadata WHERE key LIKE 'notice.c.source-fence.v1:%'"
                 ).fetchone()[0] == 1
            marker = json.loads(conn.execute(
                "SELECT value FROM metadata WHERE key LIKE 'notice.c.source-fence.v1:%'"
            ).fetchone()[0])
            assert marker["epoch"] == 1 and "evicted" not in marker
            conn.execute("DROP TRIGGER fixture_evict_refuse")
        assert evict_notice_cover(db, original.document, expected_epoch=1)
        with db._lock, db._connect() as conn:
            conn.execute(
                "UPDATE metadata SET value=? WHERE key LIKE 'notice.c.source-fence.v1:%'",
                ('{"epoch":2,"epoch":3,"last_seen_at":10}',),
            )
        with pytest.raises(ValueError, match="fence_corrupt"):
            read_notice_cover_write_epoch(db, original.document)
        with pytest.raises(ValueError, match="fence_corrupt"):
            publish(db, B, 30)
        assert read_notice_cover(db, original.document) is None
    finally:
        db.close()



@pytest.mark.parametrize("qualified", [False, True])
def test_changed_partial_after_eviction_does_not_shadow_last_complete_c(tmp_path, qualified):
    from cifereca.core.notice_cover_repository import (
        _opaque_key, _fence_key, evict_notice_cover, read_notice_cover_write_epoch,
        persist_notice_cover_many,
    )
    from cifereca.core.notice_c_cover_index import (
        persist_consolidated_cover_source, read_consolidated_notice_cover,
    )
    path = tmp_path / "partial-after-eviction.sqlite"
    db = Storage(path)
    try:
        a = publish(db, A, 10)
        c = persist_consolidated_cover_source(
            db, canonical_edition_id="edition:retain-partial", source=a,
            relation_proof_id="strong:fixture:retain-partial",
        )
        key = _opaque_key(a.document)
        if qualified:
            assert evict_notice_cover(db, a.document, expected_epoch=a.epoch)
            expected = read_notice_cover_write_epoch(db, a.document)
        else:
            with db._lock, db._connect() as conn:
                conn.execute("DELETE FROM metadata WHERE key=?", (key,))
            expected = None
        assert publish(db, B, 20, status="partial", epoch=expected) is None
        assert read_notice_cover(db, a.document) is None
        assert read_consolidated_notice_cover(db, c.document) == c
        with db._lock, db._connect() as conn:
            marker = json.loads(conn.execute(
                "SELECT value FROM metadata WHERE key=?", (_fence_key(key),),
            ).fetchone()[0])
        assert marker["last_seen_at"] == 20
        assert marker["epoch"] == (2 if qualified else 1)
        assert marker.get("evicted", False) is qualified
    finally:
        db.close()

    db = Storage(path)
    try:
        with pytest.raises(ValueError, match="stale_source"):
            publish(db, B, 19, epoch=expected)
        pending = admit_source_notice(
            key=KEY, parser_id="bnf-parser", parser_version="1",
            observed_at=21, status="partial", raw_fields={"media": {"cover": B}},
        )
        assert persist_notice_cover_many(
            db, [pending], field_path=("media", "cover"),
            cover_inventory_complete=True,
        ) == [None]
        assert read_notice_cover(db, a.document) is None
        assert read_consolidated_notice_cover(db, c.document) == c
        with pytest.raises(ValueError, match="stale_source"):
            publish(db, B, 20, epoch=expected)
        b = publish(db, B, 22, epoch=expected)
        assert b.epoch == (3 if qualified else 2)
        assert b.attribute.candidates[0].url == B
        assert db.entitlements() == [] and db.acquisitions() == []
    finally:
        db.close()


def test_exact_original_pending_proof_can_rehydrate(tmp_path):
    from cifereca.core.notice_cover_repository import _opaque_key
    db = Storage(tmp_path / "pending-replay.sqlite")
    try:
        a = publish(db, None, 10, status="partial")
        assert a.attribute.state == "pending"
        with db._lock, db._connect() as conn:
            conn.execute("DELETE FROM metadata WHERE key=?", (_opaque_key(a.document),))
        b = publish(db, None, 10, status="partial")
        assert b.epoch == 2 and b.attribute.state == "pending"
    finally:
        db.close()
