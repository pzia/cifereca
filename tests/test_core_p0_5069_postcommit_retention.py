"""#5069: Core postcommit history sweep reuses existing store executors."""
from __future__ import annotations

from cifereca import updater_postcommit_retention as postcommit
from cifereca import updater_transaction_maintenance as tx
from cifereca import updater_plugin_maintenance as plugins
from cifereca import updater_dev_backup_maintenance as dev
from cifereca import updater_core_backup_maintenance as core
from cifereca import updater_staging_maintenance as staging


def test_postcommit_orders_release_dependents_and_applies_exact_plans(
    tmp_path, monkeypatch,
):
    calls = []
    engines = [
        ("transactions", tx, "transaction_maintenance", "candidate_transactions"),
        ("plugins", plugins, "plugin_history_maintenance", "candidate_versions"),
        ("dev_backups", dev, "dev_backup_maintenance", "candidate_backups"),
        ("core_backups", core, "core_backup_maintenance", "candidate_backups"),
        ("staging", staging, "staging_maintenance", "candidate_stages"),
    ]
    for name, module, attribute, count_key in engines:
        def engine(*args, apply=False, acknowledge=None, name=name, key=count_key):
            calls.append((name, apply, acknowledge))
            if apply:
                assert acknowledge == "b" * 64
                return {"removed": 5}
            return {key: 5, "acknowledgement_sha256": "b" * 64}
        monkeypatch.setattr(module, attribute, engine)
    report = postcommit.postcommit_updater_retention(
        tmp_path / "runtime", tmp_path / "packages",
        tmp_path / "dev", tmp_path / "core",
    )
    assert [name for name, _, _ in calls] == [
        "transactions", "transactions", "plugins", "plugins",
        "dev_backups", "dev_backups", "core_backups", "core_backups",
        "staging", "staging",
    ]
    assert all(row == {"status": "complete", "removed": 5}
               for row in report.values())


def test_postcommit_unqualified_store_defers_but_others_continue(
    tmp_path, monkeypatch,
):
    events = []

    def blocked(*args, **kwargs):
        events.append("blocked")
        raise ValueError("inventory_unqualified")

    def empty(*args, **kwargs):
        assert not kwargs
        events.append("empty")
        return {"candidate_versions": 0, "acknowledgement_sha256": "0" * 64}

    def counted(key):
        def inner(*args, apply=False, acknowledge=None):
            events.append("apply" if apply else "plan")
            return {"removed": 1} if apply else {
                key: 1, "acknowledgement_sha256": "a" * 64,
            }
        return inner

    monkeypatch.setattr(tx, "transaction_maintenance", blocked)
    monkeypatch.setattr(plugins, "plugin_history_maintenance", empty)
    monkeypatch.setattr(dev, "dev_backup_maintenance", counted("candidate_backups"))
    monkeypatch.setattr(core, "core_backup_maintenance", counted("candidate_backups"))
    monkeypatch.setattr(staging, "staging_maintenance", counted("candidate_stages"))
    report = postcommit.postcommit_updater_retention(
        tmp_path / "runtime", tmp_path / "packages",
        tmp_path / "dev", tmp_path / "core",
    )
    assert report["transactions"] == {"status": "deferred", "removed": None}
    assert report["plugins"] == {"status": "complete", "removed": 0}
    assert report["staging"] == {"status": "complete", "removed": 1}
    assert events == ["blocked", "empty", "plan", "apply", "plan", "apply", "plan", "apply"]


def test_postcommit_does_not_allocate_missing_stores(tmp_path):
    runtime, packages, devstore, corestore = (
        tmp_path / "runtime", tmp_path / "plugins",
        tmp_path / "dev", tmp_path / "core",
    )
    results = postcommit.postcommit_updater_retention(
        runtime, packages, devstore, corestore,
    )
    assert len(results) == 5
    assert all(item["removed"] in (0, None) for item in results.values())
