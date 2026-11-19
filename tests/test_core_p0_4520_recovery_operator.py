"""#4520: distinct operator review and confirmation, no new physical link."""
from pathlib import Path
from types import SimpleNamespace as NS
import os

import pytest

from cifereca.core import derived_variant_recovery_operator as operator
from cifereca.core import derived_variant_supervised_recovery as recovery
from cifereca.core import derived_variant_journal as journal
from cifereca.locking import InterProcessLock
from tests.test_core_p0_4520_derived_journal import _prepared_publication


def _setup(tmp_path):
    service, intent, reservation = _prepared_publication(tmp_path)
    owner = NS(identity_id="id1", directory_name="library")
    service.current_identity = owner
    service.identity_by_id = lambda identity: owner if identity == "id1" else None
    service.config.identities = lambda: [{"id": "id1", "directory_name": "library"}]
    service._source_context_epoch = service._account_context_epoch = 1
    return service, intent, reservation


def _review(service, reservation, **kw):
    return operator.review_linked_recovery(
        service, identity_id="id1", reservation=reservation,
        **{"explicitly_requested": True, **kw},
    )


def test_review_then_confirmation_only_once(tmp_path, monkeypatch):
    service, intent, reservation = _setup(tmp_path)
    db = service.storage
    lock_path = tmp_path / "acquisition.lock"
    monkeypatch.setattr(recovery, "acquisition_lock_file", lambda: lock_path)
    try:
        reviewed = _review(service, reservation)
        assert reviewed is not None and reservation.key not in repr(reviewed)
        assert journal.summarize_derived_journal(db)["pending_count"] == 1
        monkeypatch.setattr(os, "link", lambda *a, **k: pytest.fail("must not link"))
        with InterProcessLock(lock_path) as lock:
            assert operator.execute_confirmed_linked_recovery(
                service, reviewed, shared_lock=lock,
            )["status"] == "review_required"
            assert operator.execute_confirmed_linked_recovery(
                service, reviewed, confirmed=True, shared_lock=lock,
            )["status"] == "committed"
            assert operator.execute_confirmed_linked_recovery(
                service, reviewed, confirmed=True, shared_lock=lock,
            )["status"] == "review_required"
        assert journal.summarize_derived_journal(db)["committed_count"] == 1
        assert Path(intent.staged_path).samefile(intent.candidate_path)
    finally:
        db.close()


@pytest.mark.parametrize("change", ["source", "navigation", "target", "lock"])
def test_review_revoked_or_failed_requires_new_review(tmp_path, monkeypatch, change):
    service, intent, reservation = _setup(tmp_path)
    db = service.storage
    lock_path = tmp_path / "acquisition.lock"
    monkeypatch.setattr(recovery, "acquisition_lock_file", lambda: lock_path)
    try:
        reviewed = _review(service, reservation)
        assert reviewed is not None
        if change == "source":
            service._source_context_epoch += 1
        elif change == "navigation":
            service._account_context_epoch += 2
        elif change == "target":
            target = Path(intent.candidate_path)
            target.unlink()
            target.write_bytes(b"new bytes")
        with InterProcessLock(lock_path) as lock:
            actual = None if change == "lock" else lock
            result = operator.execute_confirmed_linked_recovery(
                service, reviewed, confirmed=True, shared_lock=actual,
            )
            assert result["status"] == "review_required"
            assert operator.execute_confirmed_linked_recovery(
                service, reviewed, confirmed=True, shared_lock=lock,
            )["status"] == "review_required"
        assert journal.summarize_derived_journal(db)["pending_count"] == 1
        assert len(db.placements()) == 1
        assert Path(intent.staged_path).read_bytes() == b"new bytes"
    finally:
        db.close()


def test_no_review_when_unlinked_or_not_requested(tmp_path):
    service, intent, reservation = _setup(tmp_path)
    try:
        assert _review(service, reservation, explicitly_requested=False) is None
        Path(intent.candidate_path).unlink()
        assert _review(service, reservation) is None
        assert journal.summarize_derived_journal(service.storage)["pending_count"] == 1
    finally:
        service.storage.close()
