"""#4520: explicit recovery of an already linked receipt, zero new link."""
from dataclasses import replace
from pathlib import Path
import os

import pytest

from cifereca.core import derived_variant_journal as journal
from cifereca.core import derived_variant_publication as publication
from cifereca.core import derived_variant_supervised_recovery as recovery
from cifereca.locking import InterProcessLock
from tests.test_core_p0_4520_derived_journal import _prepared_publication


def _recover(db, root, reservation, lock, **kwargs):
    options = dict(library_root=root, identity_id="id1", reservation=reservation,
                   approved_receipt_sha256=reservation.receipt_sha256,
                   shared_lock=lock, scope_current=lambda _db: True)
    options.update(kwargs)
    return recovery.recover_linked_derived_publication(db, **options)


def test_supervised_linked_recovery_commits_without_new_link(tmp_path, monkeypatch):
    service, intent, reservation = _prepared_publication(tmp_path)
    db = service.storage
    path = tmp_path / "acquisition.lock"
    monkeypatch.setattr(recovery, "acquisition_lock_file", lambda: path)
    monkeypatch.setattr(os, "link", lambda *a, **k: pytest.fail("must not link"))
    try:
        with InterProcessLock(path) as lock:
            assert _recover(db, tmp_path, reservation, lock)["status"] == "committed"
            with pytest.raises(journal.DerivedJournalConflict, match="reservation_changed"):
                _recover(db, tmp_path, reservation, lock)
        assert journal.summarize_derived_journal(db)["committed_count"] == 1
        assert Path(intent.staged_path).samefile(intent.candidate_path)
        assert len(db.placements()) == 2
        with db._lock, db._connect() as sql:
            assert sql.execute("SELECT count(*) FROM origin_links WHERE origin_kind='derived' AND source_reference_id IS NULL").fetchone() == (1,)
    finally:
        db.close()


@pytest.mark.parametrize("failure", ["approval", "lock", "scope", "unlinked",
                                     "substituted", "revoked_during_commit"])
def test_recovery_refuses_without_exact_live_evidence(tmp_path, monkeypatch, failure):
    service, intent, reservation = _prepared_publication(tmp_path)
    db = service.storage
    path = tmp_path / "acquisition.lock"
    monkeypatch.setattr(recovery, "acquisition_lock_file", lambda: path)
    stage, target = Path(intent.staged_path), Path(intent.candidate_path)
    options = {}
    if failure == "approval":
        options["approved_receipt_sha256"] = "0" * 64
    elif failure == "scope":
        options["identity_id"] = "other"
    elif failure == "unlinked":
        target.unlink()
    elif failure == "substituted":
        target.unlink()
        target.write_bytes(b"new bytes")
    elif failure == "revoked_during_commit":
        calls = 0
        def guard(_db):
            nonlocal calls
            calls += 1
            return calls < 3
        options["scope_current"] = guard
    monkeypatch.setattr(os, "link", lambda *a, **k: pytest.fail("must not link"))
    try:
        before = db.metadata_get(reservation.key)
        with InterProcessLock(path) as lock:
            if failure == "lock":
                options["shared_lock"] = InterProcessLock(path)
            with pytest.raises((journal.DerivedJournalConflict, publication.DerivedPublicationConflict)):
                _recover(db, tmp_path, reservation, lock, **options)
        assert db.metadata_get(reservation.key) == before
        assert journal.summarize_derived_journal(db)["committed_count"] == 0
        assert len(db.placements()) == 1
        assert not db.artifact_variants()
        assert stage.read_bytes() == b"new bytes"
        if failure != "unlinked":
            assert target.read_bytes() == b"new bytes"
    finally:
        db.close()
