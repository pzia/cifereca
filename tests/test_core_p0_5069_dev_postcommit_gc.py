"""#5069: successful dev snapshot applies policy; failures defer, never rollback."""
from __future__ import annotations

from cifereca import dev_updater, updater
from cifereca import updater_dev_backup_maintenance as maintenance


def test_dev_postcommit_retention_applies_exact_plan_once(tmp_path, monkeypatch):
    root = tmp_path / "runtime"
    store = tmp_path / "state"
    monkeypatch.setattr(updater, "install_root", lambda: root)
    monkeypatch.setattr(dev_updater, "state_dir", lambda create=False: store)
    events = []

    def sweep(actual_root, backups, *, apply=False, acknowledge=None):
        assert actual_root == root
        assert backups == store / "dev-updates"
        events.append(("apply" if apply else "plan", acknowledge))
        if apply:
            assert acknowledge == "a" * 64
            return {"removed": 5}
        return {"candidate_backups": 5, "acknowledgement_sha256": "a" * 64}

    monkeypatch.setattr(maintenance, "dev_backup_maintenance", sweep)
    assert dev_updater._postcommit_dev_backup_retention() == {
        "status": "complete", "removed": 5,
    }
    assert events == [("plan", None), ("apply", "a" * 64)]


def test_dev_postcommit_retention_skips_apply_if_no_candidates(tmp_path, monkeypatch):
    monkeypatch.setattr(updater, "install_root", lambda: tmp_path / "runtime")
    monkeypatch.setattr(dev_updater, "state_dir", lambda create=False: tmp_path / "state")
    calls = []

    def sweep(*args, **kwargs):
        calls.append(kwargs)
        return {"candidate_backups": 0, "acknowledgement_sha256": "b" * 64}

    monkeypatch.setattr(maintenance, "dev_backup_maintenance", sweep)
    assert dev_updater._postcommit_dev_backup_retention() == {
        "status": "complete", "removed": 0,
    }
    assert calls == [{}]


def test_unqualified_dev_gc_defers_after_success(tmp_path, monkeypatch):
    monkeypatch.setattr(updater, "install_root", lambda: tmp_path / "runtime")
    monkeypatch.setattr(dev_updater, "state_dir", lambda create=False: tmp_path / "state")
    monkeypatch.setattr(
        maintenance, "dev_backup_maintenance",
        lambda *_, **__: (_ for _ in ()).throw(ValueError("owner_unqualified")),
    )
    assert dev_updater._postcommit_dev_backup_retention() == {
        "status": "deferred", "removed": None,
    }
