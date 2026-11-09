"""P0 #5069: DevUpdater must share Core's mutation lock and recovery gate."""

from contextlib import contextmanager

import pytest

from cifereca import dev_updater, updater


def test_dev_snapshot_uses_shared_lock_before_recovery_preflight(monkeypatch, tmp_path):
    events = []

    @contextmanager
    def locked(root):
        events.append(("lock", root))
        try:
            yield
        finally:
            events.append(("unlock", root))

    def blocked(root):
        events.append(("preflight", root))
        raise updater.UpdateError("restoration_incomplete")

    monkeypatch.setattr(updater, "install_root", lambda: tmp_path)
    monkeypatch.setattr(updater, "_exclusive_updater_lock", locked)
    monkeypatch.setattr(updater, "_preflight_incomplete_recovery", blocked)

    with pytest.raises(updater.UpdateError, match="restoration_incomplete"):
        dev_updater.apply_snapshot(tmp_path / "nonexistent.zip")

    assert events == [("lock", tmp_path), ("preflight", tmp_path), ("unlock", tmp_path)]


def test_dev_snapshot_dry_run_does_not_acquire_mutation_lock(monkeypatch, tmp_path):
    @contextmanager
    def forbidden_lock(root):
        raise AssertionError("dry-run must not acquire updater mutation lock")
        yield

    monkeypatch.setattr(updater, "_exclusive_updater_lock", forbidden_lock)
    monkeypatch.setattr(updater, "_preflight_incomplete_recovery", lambda root: (_ for _ in ()).throw(AssertionError("preflight during dry-run")))
    with pytest.raises(dev_updater.DevUpdateError):
        dev_updater.apply_snapshot(tmp_path / "nonexistent.zip", dry_run=True)
