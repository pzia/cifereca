"""#4520: atomic, bounded private reservation foundation."""
from dataclasses import replace
import hashlib
from pathlib import Path
from threading import Barrier
from concurrent.futures import ThreadPoolExecutor

import pytest

from cifereca.core import derived_variant_journal as journal
from cifereca.storage import Storage
from tests.test_core041_artifact_authority import make_service


def _fixture(tmp_path):
    service, source, pid = make_service(tmp_path)
    path = Path(service.storage.placement_get(pid)["path"])
    info = path.stat()
    stamp = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
    return service, journal.DerivedIntent(
        identity_id="id1", parent_placement_id=pid, parent_sha256=source.sha256,
        parent_path=str(path), staged_path=str(path.parent / ".cifereca-staging/variants/staged.epub"),
        candidate_path=str(path.parent / "variant.epub"),
        output_sha256=hashlib.sha256(b"new bytes").hexdigest(), output_size=9,
        parent_stamp=stamp, output_stamp=(info.st_dev, info.st_ino + 1, 9, 1),
        transformer_id="document.transformer.test", transformer_version="1",
        profile_id="", profile_version="", options_fingerprint="opt",
        variant_kind="optimized", media_format="epub",
    )


def test_reservation_durable_and_redacted(tmp_path):
    service, intent = _fixture(tmp_path)
    db = service.storage
    try:
        sync_before = db._connect().execute("PRAGMA synchronous").fetchone()
        key = journal.reserve_derived_intent(db, intent)
        assert db._connect().execute("PRAGMA synchronous").fetchone() == sync_before
        assert len(db.placements()) == 1
        assert not db.artifact_variants()
        path = db.path
        db.close()
        reopened = Storage(path)
        try:
            summary = journal.summarize_derived_journal(reopened)
            assert summary["count"] == 1
            assert summary["payload_bytes"] > 0
            assert summary["recovery_authorized"] is False
            assert summary["destructive_cleanup_allowed"] is False
            assert str(tmp_path) not in repr(summary)
            assert intent.parent_sha256 not in repr(summary)
            assert reopened.metadata_get(key) is not None
        finally:
            reopened.close()
    finally:
        db.close()


def test_two_connections_cannot_reserve_same_candidate(tmp_path):
    service, intent = _fixture(tmp_path)
    db = service.storage
    second = Storage(db.path)
    barrier = Barrier(2)

    def reserve(storage, value):
        barrier.wait(timeout=10)
        try:
            return journal.reserve_derived_intent(storage, value)
        except journal.DerivedJournalConflict as exc:
            return str(exc)

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            a = pool.submit(reserve, db, intent)
            b = pool.submit(reserve, second, replace(intent, options_fingerprint="other"))
            results = [a.result(timeout=20), b.result(timeout=20)]
        assert sum(x.startswith(journal._PREFIX) for x in results) == 1
        assert "derived_journal_slot_occupied" in results
        assert journal.summarize_derived_journal(db)["count"] == 1
    finally:
        second.close()
        db.close()


def test_quota_refuses_without_eviction_and_parent_is_exact(tmp_path, monkeypatch):
    service, intent = _fixture(tmp_path)
    db = service.storage
    try:
        with pytest.raises(journal.DerivedJournalConflict, match="parent_changed"):
            journal.reserve_derived_intent(db, replace(intent, identity_id="other"))
        monkeypatch.setattr(journal, "_MAX_RECORDS", 1)
        key = journal.reserve_derived_intent(db, intent)
        prior = db.metadata_get(key)
        with pytest.raises(journal.DerivedJournalConflict, match="capacity_exceeded"):
            journal.reserve_derived_intent(db, replace(
                intent, options_fingerprint="other",
                candidate_path=str(Path(intent.candidate_path).with_name("other.epub")),
            ))
        assert db.metadata_get(key) == prior
        assert journal.summarize_derived_journal(db)["count"] == 1
        assert len(db.placements()) == 1
    finally:
        db.close()


def test_corrupt_payload_is_not_reused_or_overwritten(tmp_path):
    service, intent = _fixture(tmp_path)
    db = service.storage
    try:
        key = journal.reserve_derived_intent(db, intent)
        db.metadata_set(key, '{"schema":"derived.intent.v2","schema":"duplicate"}')
        before = db.metadata_get(key)
        with pytest.raises(journal.DerivedJournalConflict, match="corrupt"):
            journal.summarize_derived_journal(db)
        with pytest.raises(journal.DerivedJournalConflict, match="corrupt"):
            journal.reserve_derived_intent(db, replace(intent, options_fingerprint="new"))
        assert db.metadata_get(key) == before
    finally:
        db.close()


def _prepared_publication(tmp_path):
    service, intent = _fixture(tmp_path)
    stage = Path(intent.staged_path)
    stage.parent.mkdir(parents=True)
    stage.write_bytes(b"new bytes")
    info = stage.stat()
    intent = replace(intent, output_stamp=(
        info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
    ))
    reservation = journal.reserve_derived_publication(service.storage, intent)
    import os
    os.link(stage, intent.candidate_path)
    return service, intent, reservation


def _publish_reserved(storage, intent, reservation, **extra):
    from cifereca.core.derived_variant_publication import publish_derived_variant
    return publish_derived_variant(
        storage, parent_artifact_id="sha256:" + intent.parent_sha256,
        parent_sha256=intent.parent_sha256,
        parent_placement_id=intent.parent_placement_id, parent_path=Path(intent.parent_path),
        identity_id=intent.identity_id, published_path=Path(intent.candidate_path),
        output_sha256=intent.output_sha256, output_size=intent.output_size,
        media_format=intent.media_format, mime_type="application/epub+zip",
        variant_kind=intent.variant_kind, transformer_id=intent.transformer_id,
        transformer_version=intent.transformer_version, profile_id=None,
        profile_version=intent.profile_version, options_fingerprint=intent.options_fingerprint,
        book_id="book-1", warnings=(), facts={}, journal_reservation=reservation, **extra,
    )


def test_commit_moves_exact_receipt_with_canonical_origin(tmp_path):
    service, intent, reservation = _prepared_publication(tmp_path)
    db = service.storage
    try:
        prior = db.metadata_get(reservation.key)
        sync_before = db._connect().execute("PRAGMA synchronous").fetchone()
        placement_id = _publish_reserved(db, intent, reservation)
        assert db._connect().execute("PRAGMA synchronous").fetchone() == sync_before
        assert db.metadata_get(reservation.key) is None
        assert db.metadata_get(journal._COMMITTED + reservation.receipt_sha256) == prior
        summary = journal.summarize_derived_journal(db)
        assert (summary["count"], summary["pending_count"], summary["committed_count"]) == (1, 0, 1)
        assert db.placement_get(placement_id)["path"] == intent.candidate_path
        assert Path(intent.staged_path).read_bytes() == Path(intent.candidate_path).read_bytes()
        with db._lock, db._connect() as sql:
            assert sql.execute("SELECT count(*) FROM origin_links WHERE origin_kind='derived'").fetchone() == (1,)
        # A completed receipt does not monopolize a recipe forever. A new
        # target may reserve a new slot, but history remains quota-accounted.
        new = replace(intent, candidate_path=str(Path(intent.candidate_path).with_name("next.epub")))
        journal.reserve_derived_publication(db, new)
        assert journal.summarize_derived_journal(db)["count"] == 2
    finally:
        db.close()


@pytest.mark.parametrize("failure", ["wrong_receipt", "changed_inode", "crash_after_transition"])
def test_reservation_failure_keeps_pending_and_rolls_back_canonical_rows(tmp_path, monkeypatch, failure):
    from cifereca.core import derived_variant_publication as publication
    service, intent, reservation = _prepared_publication(tmp_path)
    db = service.storage
    try:
        raw = db.metadata_get(reservation.key)
        submitted = reservation
        if failure == "wrong_receipt":
            submitted = replace(reservation, receipt_sha256="0" * 64)
        elif failure == "changed_inode":
            candidate = Path(intent.candidate_path)
            candidate.unlink()
            candidate.write_bytes(b"new bytes")
        else:
            original = publication.commit_derived_reservation_in_transaction

            def fail_after(*args, **kwargs):
                original(*args, **kwargs)
                raise journal.DerivedJournalConflict("synthetic_crash_after_transition")

            monkeypatch.setattr(publication, "commit_derived_reservation_in_transaction", fail_after)
        with pytest.raises(publication.DerivedPublicationConflict):
            _publish_reserved(db, intent, submitted)
        assert db.metadata_get(reservation.key) == raw
        assert journal.summarize_derived_journal(db)["committed_count"] == 0
        assert len(db.placements()) == 1
        assert not db.artifact_variants()
        with db._lock, db._connect() as sql:
            assert sql.execute("SELECT count(*) FROM origin_links WHERE origin_kind='derived'").fetchone() == (0,)
        assert Path(intent.candidate_path).read_bytes() == b"new bytes"
    finally:
        db.close()


def test_reserved_output_budget_includes_committed_history(tmp_path, monkeypatch):
    service, intent, reservation = _prepared_publication(tmp_path)
    db = service.storage
    try:
        monkeypatch.setattr(journal, "_MAX_RESERVED_OUTPUT_BYTES", 18)
        _publish_reserved(db, intent, reservation)
        next_intent = replace(intent, candidate_path=str(Path(intent.candidate_path).with_name("next.epub")))
        second = journal.reserve_derived_publication(db, next_intent)
        before = journal.summarize_derived_journal(db)
        assert before["pending_output_bytes"] == 9
        assert before["committed_output_bytes"] == 9
        assert before["reserved_output_bytes"] == before["reserved_output_capacity"] == 18
        assert before["physical_disk_usage_measured"] is False
        with pytest.raises(journal.DerivedJournalConflict, match="output_capacity_exceeded"):
            journal.reserve_derived_publication(db, replace(
                next_intent, options_fingerprint="new",
                staged_path=str(Path(intent.staged_path).with_name("new.epub")),
                candidate_path=str(Path(intent.candidate_path).with_name("third.epub")),
            ))
        assert journal.summarize_derived_journal(db) == before
        assert db.metadata_get(second.key) is not None
        assert Path(intent.staged_path).samefile(intent.candidate_path)
    finally:
        db.close()


def test_competing_distinct_reservations_share_one_output_budget(tmp_path, monkeypatch):
    service, intent = _fixture(tmp_path)
    db = service.storage
    second = Storage(db.path)
    monkeypatch.setattr(journal, "_MAX_RESERVED_OUTPUT_BYTES", 9)
    other = replace(
        intent, options_fingerprint="other",
        staged_path=str(Path(intent.staged_path).with_name("other.epub")),
        candidate_path=str(Path(intent.candidate_path).with_name("other.epub")),
    )
    barrier = Barrier(2)

    def reserve(storage, value):
        barrier.wait(timeout=10)
        try:
            return journal.reserve_derived_intent(storage, value)
        except journal.DerivedJournalConflict as exc:
            return str(exc)

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            a = pool.submit(reserve, db, intent)
            b = pool.submit(reserve, second, other)
            results = [a.result(timeout=20), b.result(timeout=20)]
        assert sum(x.startswith(journal._PREFIX) for x in results) == 1
        assert "derived_journal_output_capacity_exceeded" in results
        assert journal.summarize_derived_journal(db)["reserved_output_bytes"] == 9
    finally:
        second.close()
        db.close()


def test_pending_reservation_blob_receipt_fails_closed_without_mutation(tmp_path):
    """A SQLite BLOB in the TEXT ledger is not a replayable receipt."""
    import sqlite3

    service, intent, reservation = _prepared_publication(tmp_path)
    db = service.storage
    try:
        original = db.metadata_get(reservation.key)
        with db._lock, db._connect() as sql:
            sql.execute(
                "UPDATE metadata SET value=? WHERE key=?",
                (sqlite3.Binary(original.encode("ascii")), reservation.key),
            )
        conn = db._connect()
        conn.execute("BEGIN")
        try:
            with pytest.raises(
                journal.DerivedJournalConflict,
                match="derived_journal_reservation_changed",
            ):
                journal._read_pending_reservation(conn, reservation)
            assert conn.in_transaction
        finally:
            conn.rollback()
        assert db._connect().execute(
            "SELECT value FROM metadata WHERE key=?", (reservation.key,),
        ).fetchone() == (original.encode("ascii"),)
        assert Path(intent.staged_path).samefile(intent.candidate_path)
    finally:
        db.close()


@pytest.mark.parametrize("corruption", ["blob", "oversized"])
def test_canonical_origin_receipt_requires_bounded_text(tmp_path, corruption):
    """Even matching canonical fields cannot authorize a BLOB or oversized receipt."""
    import json

    service, intent = _fixture(tmp_path)
    placement_id, origin_id, recipe_id = "derived-placement", "derived-origin", "derived-recipe"
    metadata = json.dumps({
        "source_placement_id": intent.parent_placement_id,
        "transformer_plugin_id": intent.transformer_id,
        "transformer_version": intent.transformer_version,
        "profile_plugin_id": intent.profile_id,
        "profile_version": intent.profile_version,
        "options_fingerprint": intent.options_fingerprint,
        "source_sha256": intent.parent_sha256,
        "result_sha256": intent.output_sha256,
    })
    if corruption == "blob":
        metadata = metadata.encode("utf-8")
    else:
        metadata = metadata[:-1] + ', "padding": "' + "x" * 8192 + '"}'
    artifact_id = "sha256:" + intent.output_sha256
    parent_id = "sha256:" + intent.parent_sha256
    rows = iter((
        (artifact_id, intent.identity_id, intent.candidate_path, "present", "cifereca"),
        (intent.output_sha256, intent.output_size),
        ("derived", intent.identity_id, artifact_id, None, parent_id,
         f"derived:{recipe_id}:{placement_id}", metadata),
        (artifact_id, parent_id, intent.parent_sha256,
         intent.transformer_id, intent.options_fingerprint),
    ))

    class ReadOnlyDB:
        def execute(self, *_args):
            return self

        def fetchone(self):
            return next(rows)

    try:
        with pytest.raises(journal.DerivedJournalConflict, match="canonical_proof_missing"):
            journal._require_canonical_proof(
                ReadOnlyDB(), intent, placement_id=placement_id,
                origin_id=origin_id, recipe_id=recipe_id,
            )
    finally:
        service.storage.close()


def test_historical_committed_receipt_keeps_target_reserved_even_without_placement(tmp_path):
    """An incomplete/corrupt historical B state is not a free destination.

    Simulate a committed journal row after the canonical target placement was
    lost. A new recipe must not adopt the same path merely because the
    placements index has no row for it.
    """
    service, intent = _fixture(tmp_path)
    db = service.storage
    try:
        reservation = journal.reserve_derived_publication(db, intent)
        raw = db.metadata_get(reservation.key)
        historical = journal._COMMITTED + reservation.receipt_sha256
        with db._lock, db._connect() as sql:
            sql.execute("DELETE FROM metadata WHERE key=?", (reservation.key,))
            sql.execute("INSERT INTO metadata(key,value) VALUES(?,?)", (historical, raw))
        assert not any(row["path"] == intent.candidate_path for row in db.placements())
        second = replace(
            intent, options_fingerprint="different-recipe",
            staged_path=str(Path(intent.staged_path).with_name("second-stage.epub")),
        )
        with pytest.raises(journal.DerivedJournalConflict, match="slot_occupied"):
            journal.reserve_derived_publication(db, second)
        assert db.metadata_get(historical) == raw
        assert db.metadata_get(journal._slot(second)) is None
        assert journal.summarize_derived_journal(db)["count"] == 1

        independent = replace(
            second, candidate_path=str(Path(intent.candidate_path).with_name("independent.epub")),
        )
        journal.reserve_derived_publication(db, independent)
        assert journal.summarize_derived_journal(db)["count"] == 2
    finally:
        db.close()


@pytest.mark.parametrize("hidden", ["\x85", "\u202e", "\u2028", "\u200d", "\ud800"])
@pytest.mark.parametrize("field", ["transformer_id", "options_fingerprint", "candidate_path"])
def test_invisible_unicode_receipt_coordinate_is_rejected_without_reservation(
    tmp_path, hidden, field,
):
    service, intent = _fixture(tmp_path)
    storage = service.storage
    try:
        baseline = journal.summarize_derived_journal(storage)
        initial = len(storage.placements())
        if field == "candidate_path":
            value = str(Path(intent.candidate_path).with_name("variant" + hidden + ".epub"))
        elif field == "options_fingerprint":
            value = "option" + hidden + "fingerprint"
        else:
            value = "transformer" + hidden + "test"
        changed = replace(intent, **{field: value})
        with pytest.raises(journal.DerivedJournalConflict, match="invalid_intent"):
            journal.reserve_derived_intent(storage, changed)
        assert journal.summarize_derived_journal(storage) == baseline
        assert len(storage.placements()) == initial
        assert not storage.artifact_variants()
    finally:
        storage.close()
