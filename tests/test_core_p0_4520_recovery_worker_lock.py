"""#4520: recovery worker uses the same OS lock but no unsafe lock-file writes."""
from pathlib import Path
import os

import pytest

from cifereca.core import derived_variant_recovery_operator as operator
from cifereca.core import derived_variant_supervised_recovery as recovery
from cifereca.core import derived_variant_journal as journal
from cifereca.locking import InterProcessLock, LockBusyError
from tests.test_core_p0_4520_recovery_operator import _setup, _review


pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX strict lock")


def _paths(monkeypatch, path):
    def configured(*, create=True):
        assert create is False
        return path
    monkeypatch.setattr(operator, "acquisition_lock_file", configured)
    monkeypatch.setattr(recovery, "acquisition_lock_file", configured)


def test_worker_consumes_confirmed_review_without_touching_existing_lock(tmp_path, monkeypatch):
    service, intent, reservation = _setup(tmp_path)
    db = service.storage
    path = tmp_path / "local-acquisition.lock"
    path.write_bytes(b"prior owner diagnostic")
    path.chmod(0o600)
    _paths(monkeypatch, path)
    try:
        review = _review(service, reservation)
        assert review is not None
        assert operator.run_confirmed_linked_recovery(service, review)["status"] == "review_required"
        before = path.stat()
        assert operator.run_confirmed_linked_recovery(service, review, confirmed=True)["status"] == "committed"
        assert path.read_bytes() == b"prior owner diagnostic"
        assert path.stat().st_mtime_ns == before.st_mtime_ns
        assert journal.summarize_derived_journal(db)["committed_count"] == 1
        assert Path(intent.staged_path).samefile(intent.candidate_path)
        assert operator.run_confirmed_linked_recovery(service, review, confirmed=True)["status"] == "review_required"
    finally:
        db.close()


@pytest.mark.parametrize("case", ["missing", "symlink", "ancestor", "hardlink", "fifo", "writable", "busy"])
def test_unsafe_or_busy_lock_refuses_without_mutation(tmp_path, monkeypatch, case):
    service, intent, reservation = _setup(tmp_path)
    db = service.storage
    path = tmp_path / "local-acquisition.lock"
    victim = tmp_path / "unrelated"
    victim.write_bytes(b"preserve")
    victim.chmod(0o600)
    held = None
    if case == "symlink":
        path.symlink_to(victim)
    elif case == "ancestor":
        actual = tmp_path / "actual"
        actual.mkdir()
        (actual / "local-acquisition.lock").write_bytes(b"preserve")
        alias = tmp_path / "alias"
        alias.symlink_to(actual, target_is_directory=True)
        path = alias / path.name
    elif case == "hardlink":
        os.link(victim, path)
    elif case == "fifo":
        os.mkfifo(path, 0o600)
    elif case == "writable":
        path.write_bytes(b"preserve")
        path.chmod(0o666)
    elif case == "busy":
        held = InterProcessLock(path)
        held.acquire()
    _paths(monkeypatch, path)
    try:
        review = _review(service, reservation)
        assert review is not None
        before = db.metadata_get(reservation.key)
        assert operator.run_confirmed_linked_recovery(service, review, confirmed=True)["status"] == "review_required"
        assert db.metadata_get(reservation.key) == before
        assert len(db.placements()) == 1
        assert Path(intent.staged_path).samefile(intent.candidate_path)
        assert victim.read_bytes() == b"preserve"
        if case == "missing":
            assert not path.exists()
        if case in ("writable", "ancestor"):
            assert path.read_bytes() == b"preserve"
        if held is not None:
            held.release()
            held = None
            assert operator.run_confirmed_linked_recovery(service, review, confirmed=True)["status"] == "review_required"
    finally:
        if held is not None:
            held.release()
        db.close()


def test_strict_lock_is_same_flock_as_historical_lock(tmp_path):
    path = tmp_path / "lock"
    path.write_text("preserve")
    path.chmod(0o600)
    with InterProcessLock(path, existing_nofollow=True) as strict:
        assert strict.strict_current()
        with pytest.raises(LockBusyError):
            InterProcessLock(path).acquire()
        assert path.read_text() == "preserve"
    assert not strict.strict_current()
