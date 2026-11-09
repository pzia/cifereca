"""P0 #5069: abandoned staging is reclaimed only with producer receipts."""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json

from cifereca import updater
from cifereca.updater_staging_maintenance import staging_maintenance


NOW = 2_000_000_000.0


def _iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat()


def _stamp(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _stage_tx(root, age, n):
    stamp = _stamp(NOW - age - n * 60)
    name = f"{stamp}-{n + 1:012x}"
    folder = root / "transactions" / f".{name}.123.tmp"
    folder.mkdir(parents=True)
    (folder / "transaction.json").write_text(json.dumps({
        "schema": updater.TRANSACTION_SCHEMA,
        "id": name, "state": "prepared", "created_at": stamp,
        "core": {}, "packages": {},
    }))
    (folder / "plugin-backup.bin").write_bytes(b"owned scratch")
    return folder


def _stage_core_backup(backups, age):
    stamp = _stamp(NOW - age)
    label = "updater-only-fixture"
    digest = sha256(label.encode()).hexdigest()[:10]
    folder = backups / f".{stamp}-{digest}.123.tmp"
    folder.mkdir(parents=True)
    (folder / "backup.json").write_text(json.dumps({
        "label": label, "created_at": stamp,
    }))
    (folder / "sample.sqlite3").write_bytes(b"fixture backup")
    return folder


def _stage_plugin(packages, age):
    folder = packages / "org.test" / ".1.0.0-abcdefgh"
    folder.mkdir(parents=True)
    (folder / "cifereca-plugin.json").write_text(json.dumps({
        "package": {"id": "org.test", "version": "1.0.0"},
    }))
    (folder / ".cifereca-install.json").write_text(json.dumps({
        "sha256": "a" * 64, "installed_at": _iso(NOW - age),
    }))
    (folder / "module.py").write_text("pass")
    return folder


def _roots(tmp_path):
    return (tmp_path / "runtime",
            tmp_path / "state" / "plugins" / "packages",
            tmp_path / "state" / "update-backups")


def test_owned_old_scratch_removed_without_touching_unknown(tmp_path, monkeypatch):
    runtime, packages, backups = _roots(tmp_path)
    monkeypatch.setattr(updater, "_trusted_package_lease",
                        lambda: __import__("contextlib").nullcontext())
    old = [_stage_tx(runtime, 3 * 86400, n) for n in range(7)]
    old += [_stage_core_backup(backups, 3 * 86400),
            _stage_plugin(packages, 3 * 86400)]
    unknown = packages / "org.test" / ".1.0.1-abcdefgh"
    unknown.mkdir()
    (unknown / "personal.txt").write_text("KEEP")
    plan = staging_maintenance(runtime, packages, backups, now=NOW)
    assert plan["candidate_stages"] == 9
    assert plan["retained_unqualified"] >= 1
    outcome = staging_maintenance(
        runtime, packages, backups, now=NOW, apply=True,
        acknowledge=plan["acknowledgement_sha256"],
    )
    assert outcome["removed"] == 9
    assert all(not stage.exists() for stage in old)
    assert (unknown / "personal.txt").read_text() == "KEEP"


def test_recent_scratch_and_invalid_old_scratch_preserved(tmp_path):
    runtime, packages, backups = _roots(tmp_path)
    recent = _stage_tx(runtime, 30, 0)
    old = _stage_tx(runtime, 3 * 86400, 1)
    (old / "transaction.json").unlink()
    plan = staging_maintenance(runtime, packages, backups, now=NOW)
    assert plan["candidate_stages"] == 0
    assert recent.is_dir() and old.is_dir()


def test_wrong_acknowledgement_has_no_effect(tmp_path, monkeypatch):
    runtime, packages, backups = _roots(tmp_path)
    monkeypatch.setattr(updater, "_trusted_package_lease",
                        lambda: __import__("contextlib").nullcontext())
    stage = _stage_tx(runtime, 4 * 86400, 0)
    try:
        staging_maintenance(
            runtime, packages, backups, now=NOW,
            apply=True, acknowledge="0" * 64,
        )
    except ValueError as error:
        assert "plan_changed" in str(error)
    else:
        raise AssertionError("wrong acknowledgement admitted")
    assert stage.is_dir()


def test_missing_staging_roots_are_read_only(tmp_path):
    runtime, packages, backups = _roots(tmp_path)
    plan = staging_maintenance(runtime, packages, backups, now=NOW)
    assert plan["candidate_stages"] == 0
    assert not runtime.exists() and not packages.exists() and not backups.exists()


def test_staging_cli_plan_does_not_create_roots(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(updater, "install_root", lambda: tmp_path / "runtime")
    monkeypatch.setattr(updater, "_plugin_packages_root", lambda: tmp_path / "packages")
    monkeypatch.setattr(updater, "state_dir", lambda create=False: tmp_path / "state")
    assert updater.main(["--maintenance-staging", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["mode"] == "plan"
    assert result["candidate_stages"] == 0
    assert not (tmp_path / "state").exists()


def test_crash_after_staging_quarantine_rename_resumes(tmp_path, monkeypatch):
    from cifereca import updater_staging_maintenance as maintenance
    runtime, packages, backups = _roots(tmp_path)
    monkeypatch.setattr(updater, "_trusted_package_lease",
                        lambda: __import__("contextlib").nullcontext())
    stage = _stage_tx(runtime, 4 * 86400, 0)
    plan = staging_maintenance(runtime, packages, backups, now=NOW)
    original = maintenance.shutil.rmtree
    interrupted = False

    def crash(path):
        nonlocal interrupted
        if not interrupted:
            interrupted = True
            raise OSError("synthetic outage")
        return original(path)

    monkeypatch.setattr(maintenance.shutil, "rmtree", crash)
    try:
        staging_maintenance(runtime, packages, backups, now=NOW,
                            apply=True, acknowledge=plan["acknowledgement_sha256"])
    except OSError:
        pass
    else:
        raise AssertionError("synthetic crash not observed")
    assert not stage.exists()
    quarantine = runtime / "staging-gc"
    assert any(child.is_dir() for child in quarantine.iterdir())
    monkeypatch.setattr(maintenance.shutil, "rmtree", original)
    resumed = staging_maintenance(runtime, packages, backups, now=NOW)
    assert staging_maintenance(
        runtime, packages, backups, now=NOW, apply=True,
        acknowledge=resumed["acknowledgement_sha256"],
    )["removed"] == 0
    assert not any(child.is_dir() for child in quarantine.iterdir())
