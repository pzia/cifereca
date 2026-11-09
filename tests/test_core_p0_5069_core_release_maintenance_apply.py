"""P0 #5069: reclaim existing Core releases under explicit plan/ack."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os

import pytest

from cifereca import updater
from cifereca.updater_core_release_maintenance import core_release_maintenance

NOW = 2_000_000_000.0


def _release(root, number, age):
    path = root / "releases" / f"release-{number}"
    path.mkdir(parents=True)
    (path / ".cifereca-install.json").write_text(json.dumps({
        "version": f"release-{number}",
        "installed_at": datetime.fromtimestamp(
            NOW - age, timezone.utc,
        ).isoformat(),
    }))
    os.utime(path, (NOW - age, NOW - age))
    return path


def _fixture(tmp_path, monkeypatch, *, age):
    root = tmp_path / "runtime"
    paths = [_release(root, n, age + n * 60) for n in range(7)]
    updater._replace_symlink(root / "current", paths[0])
    updater._replace_symlink(root / "previous", paths[1])
    (root / "transactions").mkdir()
    monkeypatch.setattr(updater.time, "time", lambda: NOW)
    monkeypatch.setattr(updater, "_trusted_package_lease",
                        lambda: __import__("contextlib").nullcontext())
    return root, paths


def test_seven_recent_core_releases_are_all_retained(tmp_path, monkeypatch):
    root, paths = _fixture(tmp_path, monkeypatch, age=100)
    report = core_release_maintenance(root, now=NOW)
    assert report["candidate_releases"] == 0
    assert all(path.exists() for path in paths)


def test_seven_expired_core_releases_reduce_to_two(tmp_path, monkeypatch):
    root, paths = _fixture(tmp_path, monkeypatch, age=3 * 86400)
    plan = core_release_maintenance(root, now=NOW)
    assert plan["candidate_releases"] == 5
    executed = core_release_maintenance(
        root, now=NOW, apply=True,
        acknowledge=plan["acknowledgement_sha256"],
    )
    assert executed["removed"] == 5 and executed["deferred"] == 0
    assert all(path.is_dir() for path in paths[:2])
    assert all(not path.exists() for path in paths[2:])


def test_higher_keep_floor_preserves_additional_core_releases(tmp_path, monkeypatch):
    root, paths = _fixture(tmp_path, monkeypatch, age=3 * 86400)
    plan = core_release_maintenance(root, now=NOW, keep=4)
    assert plan["candidate_releases"] == 3
    result = core_release_maintenance(
        root, now=NOW, keep=4, apply=True,
        acknowledge=plan["acknowledgement_sha256"],
    )
    assert result["removed"] == 3
    assert all(path.is_dir() for path in paths[:4])


def test_wrong_acknowledgement_does_not_delete_core_release(tmp_path, monkeypatch):
    root, paths = _fixture(tmp_path, monkeypatch, age=3 * 86400)
    with pytest.raises(ValueError, match="plan_changed"):
        core_release_maintenance(
            root, now=NOW, apply=True, acknowledge="0" * 64,
        )
    assert all(path.is_dir() for path in paths)


def test_missing_runtime_plan_is_read_only(tmp_path):
    root = tmp_path / "runtime"
    report = core_release_maintenance(root, now=NOW)
    assert report["candidate_releases"] == 0
    assert not root.exists()

def test_cli_core_release_plan_readonly(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(updater, "install_root", lambda: tmp_path / "runtime")
    assert updater.main(["--maintenance-core-releases", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["mode"] == "plan"
    assert not (tmp_path / "runtime").exists()
