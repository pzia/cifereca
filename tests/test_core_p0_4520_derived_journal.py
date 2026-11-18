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
