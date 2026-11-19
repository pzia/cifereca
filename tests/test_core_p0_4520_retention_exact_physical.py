"""#4520: exact physical witness cannot be borrowed from another receipt."""
from dataclasses import replace
import hashlib
from pathlib import Path
import os

from cifereca.core import derived_variant_journal as journal
from cifereca.core import derived_variant_retention_physical as physical
from tests.test_core_p0_4520_derived_journal import _prepared_publication, _publish_reserved


def _exact(service, root, reservation, **overrides):
    options = dict(library_root=root, identity_id="id1",
                   receipt_key=journal._COMMITTED + reservation.receipt_sha256,
                   receipt_sha256=reservation.receipt_sha256,
                   explicitly_requested=True)
    options.update(overrides)
    return physical._review_exact_committed_physical(service.storage, **options)


def test_exact_witness_distinguishes_two_committed_receipts(tmp_path):
    service, intent, first = _prepared_publication(tmp_path)
    db = service.storage
    stage = Path(intent.staged_path).with_name("second-stage.epub")
    target = Path(intent.candidate_path).with_name("second.epub")
    try:
        assert _exact(service, tmp_path, first) == "unavailable"
        _publish_reserved(db, intent, first)
        stage.write_bytes(b"second derivative")
        info = stage.stat()
        other = replace(intent, staged_path=str(stage), candidate_path=str(target),
                        output_sha256=hashlib.sha256(stage.read_bytes()).hexdigest(),
                        output_size=info.st_size,
                        output_stamp=(info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns),
                        options_fingerprint="second")
        second = journal.reserve_derived_publication(db, other)
        os.link(stage, target)
        _publish_reserved(db, other, second)
        stage.unlink()
        stage.write_bytes(target.read_bytes())  # same bytes, different inode
        before = journal.summarize_derived_journal(db)
        assert _exact(service, tmp_path, first) == "linked_pair_observed"
        assert _exact(service, tmp_path, second) == "unverified"
        assert _exact(service, tmp_path, first,
                      receipt_key=journal._COMMITTED + second.receipt_sha256) == "unavailable"
        assert _exact(service, tmp_path, first, identity_id="other") == "unavailable"
        assert _exact(service, tmp_path, first, explicitly_requested=False) == "unavailable"
        assert journal.summarize_derived_journal(db) == before
    finally:
        db.close()


def test_exact_witness_discards_changed_snapshot(tmp_path, monkeypatch):
    service, intent, reservation = _prepared_publication(tmp_path)
    db = service.storage
    try:
        _publish_reserved(db, intent, reservation)
        original = physical._snapshot
        calls = 0

        def changing(storage, identity):
            nonlocal calls
            calls += 1
            directory, records = original(storage, identity)
            return directory, records if calls == 1 else ()

        monkeypatch.setattr(physical, "_snapshot", changing)
        assert _exact(service, tmp_path, reservation) == "evidence_changed"
    finally:
        db.close()


def test_exact_witness_respects_budget_and_outer_transaction(tmp_path, monkeypatch):
    service, intent, reservation = _prepared_publication(tmp_path)
    db = service.storage
    sql = db._connect()
    try:
        _publish_reserved(db, intent, reservation)
        with monkeypatch.context() as guard:
            guard.setattr(physical, "_MAX_BYTES", 1)
            assert _exact(service, tmp_path, reservation) == "budget_exceeded"
        sql.execute("BEGIN IMMEDIATE")
        sql.execute("INSERT INTO metadata(key,value) VALUES(?,?)",
                    ("test.exact.physical.uncommitted", "private"))
        assert _exact(service, tmp_path, reservation) == "unavailable"
        assert sql.in_transaction
        sql.rollback()
        assert db.metadata_get("test.exact.physical.uncommitted") is None
    finally:
        if sql.in_transaction:
            sql.rollback()
        db.close()
