"""#4520: inspect interrupted exact receipts without authorizing recovery."""
from dataclasses import replace
import os
from pathlib import Path

import pytest

from cifereca.core import derived_variant_journal as journal
from cifereca.core import derived_variant_review as review
from tests.test_core_p0_4520_derived_journal import _fixture


def _pending(tmp_path, *, linked=False):
    service, intent = _fixture(tmp_path)
    stage = Path(intent.staged_path)
    stage.parent.mkdir(parents=True)
    stage.write_bytes(b"new bytes")
    info = stage.stat()
    intent = replace(intent, output_stamp=(
        info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
    ))
    reservation = journal.reserve_derived_publication(service.storage, intent)
    if linked:
        os.link(stage, intent.candidate_path)
    return service.storage, intent, reservation


def _review(db, root, reservation, identity="id1"):
    return review.review_pending_derived_publication(
        db, library_root=root, identity_id=identity, reservation=reservation,
    )


@pytest.mark.parametrize("linked", [False, True])
def test_review_distinguishes_prelink_and_uncommitted_link(tmp_path, linked):
    db, intent, reservation = _pending(tmp_path, linked=linked)
    try:
        before = db.metadata_get(reservation.key)
        result = _review(db, tmp_path, reservation)
        assert result["status"] == ("linked_pending" if linked else "staged_only")
        assert result["recovery_authorized"] is False
        assert result["destructive_cleanup_allowed"] is False
        assert intent.parent_sha256 not in repr(result)
        assert intent.candidate_path not in repr(result)
        assert db.metadata_get(reservation.key) == before
        assert len(db.placements()) == 1
        assert not db.artifact_variants()
        assert Path(intent.staged_path).read_bytes() == b"new bytes"
        assert Path(intent.candidate_path).exists() is linked
    finally:
        db.close()


@pytest.mark.parametrize("change", ["same_bytes", "symlink", "parent", "placement"])
def test_contradictory_evidence_is_never_recoverable(tmp_path, change):
    db, intent, reservation = _pending(tmp_path)
    target = Path(intent.candidate_path)
    try:
        if change == "same_bytes":
            target.write_bytes(b"new bytes")
        elif change == "symlink":
            target.symlink_to(intent.staged_path)
        elif change == "parent":
            original = Path(intent.parent_path)
            original.rename(original.with_name("prior.epub"))
            original.write_bytes(b"book-data")
        else:
            db.placement_put({
                "artifact_id": "sha256:" + intent.parent_sha256,
                "path": intent.candidate_path, "identity_id": "foreign",
                "provider_id": "local", "state": "missing", "metadata": {},
            })
        before = db.metadata_get(reservation.key)
        assert _review(db, tmp_path, reservation)["status"] == "manual_review"
        assert db.metadata_get(reservation.key) == before
        assert Path(intent.staged_path).read_bytes() == b"new bytes"
        assert not db.artifact_variants()
        if change == "same_bytes":
            assert target.read_bytes() == b"new bytes"
        elif change == "symlink":
            assert target.is_symlink()
    finally:
        db.close()


def test_foreign_scope_and_budget_stop_before_hash(tmp_path, monkeypatch):
    db, intent, reservation = _pending(tmp_path)
    monkeypatch.setattr(review, "_verified_variant_file",
                        lambda *args, **kwargs: pytest.fail("must not hash"))
    try:
        assert _review(db, tmp_path, reservation, identity="other")["status"] == "manual_review"
        assert _review(db, tmp_path / "unrelated", reservation)["status"] == "manual_review"
        monkeypatch.setattr(review, "_MAX_REVIEW_BYTES", 1)
        assert _review(db, tmp_path, reservation)["reason"] == "review_budget_exceeded"
        assert journal.summarize_derived_journal(db)["pending_count"] == 1
    finally:
        db.close()


def test_changed_ownership_during_review_refuses(tmp_path, monkeypatch):
    db, intent, reservation = _pending(tmp_path, linked=True)
    original = review._snapshot
    calls = 0

    def rebound(*args):
        nonlocal calls
        calls += 1
        if calls == 2:
            with db._lock, db._connect() as sql:
                sql.execute("UPDATE placements SET state='missing' WHERE placement_id=?",
                            (intent.parent_placement_id,))
        return original(*args)

    monkeypatch.setattr(review, "_snapshot", rebound)
    try:
        assert _review(db, tmp_path, reservation)["status"] == "manual_review"
        assert calls == 2
        assert Path(intent.candidate_path).samefile(intent.staged_path)
        assert journal.summarize_derived_journal(db)["pending_count"] == 1
    finally:
        db.close()
