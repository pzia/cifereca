"""#4520: selection -> private synthetic recovery -> durable retention evidence."""
from dataclasses import replace
from pathlib import Path
import os

import pytest

from cifereca.core import derived_variant_journal as journal
from cifereca.core import derived_variant_publication as publication
from cifereca.core import derived_variant_recovery_operator as operator
from cifereca.core import derived_variant_recovery_selection as selection
from cifereca.core import derived_variant_stock_review as stock
from cifereca.storage import Storage
from tests.test_core_p0_4520_recovery_operator import _setup
from tests.test_core_p0_4520_recovery_worker_lock import _paths


pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX strict recovery lock")


def _select(service):
    page = selection.recovery_choices(
        service, identity_id="id1", explicitly_requested=True,
    )
    assert page is not None and len(page.choices) == 1
    choice = page.choices[0]
    return selection.review_recovery_choice(
        service, page, choice, explicitly_requested=True,
    )


def _stock(service, tmp_path):
    return stock.review_derived_staging_stock(
        service.storage, library_root=tmp_path, identity_id="id1",
    )


def _lock(tmp_path, monkeypatch):
    lock = tmp_path / "acquisition.lock"
    lock.write_bytes(b"prior diagnostic")
    lock.chmod(0o600)
    _paths(monkeypatch, lock)
    return lock


def _execute(service, reviewed):
    # Only this synthetic test translates a reviewed choice into actual
    # confirmation. No UI, plugin, production flag or provider is involved.
    return operator.run_confirmed_linked_recovery(
        service, reviewed.review, confirmed=True,
    )


def test_selected_recovery_retains_quota_and_physical_evidence_after_reopen(tmp_path, monkeypatch):
    service, intent, reservation = _setup(tmp_path)
    db = service.storage
    lock = _lock(tmp_path, monkeypatch)
    monkeypatch.setattr(journal, "_MAX_RESERVED_OUTPUT_BYTES", intent.output_size)
    stage, target = Path(intent.staged_path), Path(intent.candidate_path)
    try:
        reviewed = _select(service)
        assert reviewed is not None
        before = journal.summarize_derived_journal(db)
        physical = _stock(service, tmp_path)
        assert physical["observations"]["same_inode_target"] == 1
        assert operator.run_confirmed_linked_recovery(service, reviewed.review)["status"] == "review_required"
        with monkeypatch.context() as guard:
            def no_fs_mutation(*args, **kwargs):
                pytest.fail("already linked recovery must not link or unlink")
            guard.setattr(os, "link", no_fs_mutation)
            guard.setattr(os, "unlink", no_fs_mutation)
            assert _execute(service, reviewed)["status"] == "committed"
        after = journal.summarize_derived_journal(db)
        assert (after["pending_count"], after["committed_count"]) == (0, 1)
        for key in ("count", "payload_bytes", "reserved_output_bytes"):
            assert after[key] == before[key]
        assert after["reserved_output_bytes"] == intent.output_size
        assert _stock(service, tmp_path) == physical
        assert lock.read_bytes() == b"prior diagnostic"
        assert stage.samefile(target)
        assert _execute(service, reviewed)["status"] == "review_required"

        path = db.path
        db.close()
        db = service.storage = Storage(path)
        assert journal.summarize_derived_journal(db) == after
        assert _stock(service, tmp_path) == physical
        empty = selection.recovery_choices(service, identity_id="id1", explicitly_requested=True)
        assert empty is not None and not empty.choices
        placements = [p for p in db.placements() if p["path"] == str(target)]
        assert len(placements) == 1
        assert placements[0]["identity_id"] == "id1"
        assert placements[0]["artifact_id"] == "sha256:" + intent.output_sha256
        with db._lock, db._connect() as sql:
            assert sql.execute(
                "SELECT count(*) FROM origin_links WHERE origin_kind='derived' AND source_reference_id IS NULL"
            ).fetchone() == (1,)
        with pytest.raises(journal.DerivedJournalConflict, match="output_capacity_exceeded"):
            journal.reserve_derived_publication(db, replace(
                intent, options_fingerprint="next",
                staged_path=str(stage.with_name("next.epub")),
                candidate_path=str(target.with_name("next.epub")),
            ))
        assert journal.summarize_derived_journal(db) == after
        assert stage.samefile(target)
    finally:
        db.close()


def test_selection_worker_rollback_then_requires_fresh_review(tmp_path, monkeypatch):
    service, intent, reservation = _setup(tmp_path)
    db = service.storage
    _lock(tmp_path, monkeypatch)
    try:
        reviewed = _select(service)
        assert reviewed is not None
        before = journal.summarize_derived_journal(db)
        raw = db.metadata_get(reservation.key)
        original = publication.commit_derived_reservation_in_transaction

        def fail_after_transition(*args, **kwargs):
            original(*args, **kwargs)
            raise journal.DerivedJournalConflict("synthetic_recovery_rollback")

        with monkeypatch.context() as guard:
            guard.setattr(publication, "commit_derived_reservation_in_transaction", fail_after_transition)
            assert _execute(service, reviewed)["status"] == "review_required"
        assert db.metadata_get(reservation.key) == raw
        assert journal.summarize_derived_journal(db) == before
        assert len(db.placements()) == 1 and not db.artifact_variants()
        assert Path(intent.staged_path).samefile(intent.candidate_path)
        assert _execute(service, reviewed)["status"] == "review_required"
        fresh = _select(service)
        assert fresh is not None
        assert _execute(service, fresh)["status"] == "committed"
        assert journal.summarize_derived_journal(db)["committed_count"] == 1
    finally:
        db.close()


def test_selection_revoked_by_identity_aba_and_unlinked_receipt(tmp_path, monkeypatch):
    service, intent, reservation = _setup(tmp_path)
    db = service.storage
    _lock(tmp_path, monkeypatch)
    try:
        reviewed = _select(service)
        assert reviewed is not None
        before = journal.summarize_derived_journal(db)
        service._account_context_epoch += 2  # A -> B -> A, same owner object.
        assert _execute(service, reviewed)["status"] == "review_required"
        assert journal.summarize_derived_journal(db) == before
        Path(intent.candidate_path).unlink()
        assert _select(service) is None
        assert _stock(service, tmp_path)["observations"]["stage_only"] == 1
        assert journal.summarize_derived_journal(db) == before
        assert len(db.placements()) == 1
    finally:
        db.close()
