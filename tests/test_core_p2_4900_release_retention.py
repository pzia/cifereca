"""#4900: 24-hour deployment retention with a minimum of two rollback-safe trees.

Fixtures are local/offline: never touch a user's installation or packages.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path

import pytest

from cifereca import updater


NOW = 2_000_000_000.0


def _release(root: Path, name: str, age: float, *, metadata=True) -> Path:
    path = root / "releases" / name
    path.mkdir(parents=True)
    (path / "payload.txt").write_text(name, encoding="utf-8")
    if metadata:
        when = datetime.fromtimestamp(NOW - age, timezone.utc).isoformat()
        (path / ".cifereca-install.json").write_text(
            json.dumps({"version": "1.0.0", "installed_at": when}) + "\n",
            encoding="utf-8",
        )
    os.utime(path, (NOW - age, NOW - age))
    return path


def _pointers(root: Path, active: Path, previous: Path | None = None) -> None:
    updater._replace_symlink(root / "current", active)
    if previous is not None:
        updater._replace_symlink(root / "previous", previous)


@pytest.fixture
def clock(monkeypatch):
    monkeypatch.setattr(updater.time, "time", lambda: NOW)


def test_all_generations_from_last_24_hours_are_kept_even_if_many(tmp_path, clock):
    root = tmp_path / "runtime"
    releases = [_release(root, f"1.0.0-{i}", i * 900) for i in range(9)]
    _pointers(root, releases[0], releases[1])
    result = updater._gc(root, 2)
    assert result["removed"] == 0
    assert result["retained"] == len(releases)
    assert all(path.exists() for path in releases)


def test_boundary_and_floor_two_when_everything_is_old(tmp_path, clock, monkeypatch):
    root = tmp_path / "runtime"
    ages = [1, 24 * 3600 - 1, 24 * 3600, 24 * 3600 + 1, 3 * 86400]
    releases = [_release(root, f"v{i}", age) for i, age in enumerate(ages)]
    _pointers(root, releases[0], releases[1])
    result = updater._gc(root, 2)
    assert result["removed"] == 3
    assert [p.exists() for p in releases] == [True, True, False, False, False]
    # Run the same cleanup days later. The two most recent deployments are
    # still retained when all releases are older than one day.
    monkeypatch.setattr(updater.time, "time", lambda: NOW + 10 * 86400)
    assert updater._gc(root, 1)["retained"] == 2
    assert releases[0].exists() and releases[1].exists()


def test_explicit_keep_is_an_additive_floor_but_cannot_lower_two(tmp_path, clock):
    root = tmp_path / "runtime"
    paths = [_release(root, f"v{i}", 10 * 86400 + i * 3600) for i in range(5)]
    _pointers(root, paths[0], paths[1])
    assert updater._gc(root, 4)["retained"] == 4
    assert sum(p.exists() for p in paths) == 4
    assert updater._gc(root, 1)["retained"] == 2
    assert paths[:2] == [p for p in paths if p.exists()]


def test_same_semantic_version_with_distinct_trees_counts_as_two(tmp_path, clock):
    root = tmp_path / "runtime"
    old = _release(root, "1.0.0-commit-a", 7 * 86400)
    new = _release(root, "1.0.0-commit-b", 6 * 86400)
    other = _release(root, "0.9.0-commit-z", 8 * 86400)
    _pointers(root, new, old)
    assert updater._gc(root, 2)["retained"] == 2
    assert new.is_dir() and old.is_dir() and not other.exists()


def test_applied_transaction_protects_before_previous_for_exact_rollback(tmp_path, clock):
    root = tmp_path / "runtime"
    c = _release(root, "C", 48 * 3600)
    b = _release(root, "B", 72 * 3600)
    a = _release(root, "A", 96 * 3600)
    stale = _release(root, "stale", 8 * 86400)
    _pointers(root, c, b)
    transaction_id = "fixture-c"
    transaction_dir = root / "transactions" / transaction_id
    transaction_dir.mkdir(parents=True)
    (transaction_dir / "transaction.json").write_text(json.dumps({
        "schema": updater.TRANSACTION_SCHEMA,
        "id": transaction_id,
        "state": "applied",
        "core": {
            "before_current": {"path": str(b)},
            "before_previous": {"path": str(a)},
            "after": {"path": str(c)},
        },
        "packages": {},
    }), encoding="utf-8")
    (c / updater.TRANSACTION_FILE).write_text(
        json.dumps({"schema": 1, "id": transaction_id}), encoding="utf-8",
    )
    result = updater._gc(root, 2)
    assert result["retained"] == 3
    assert a.is_dir() and b.is_dir() and c.is_dir()
    assert not stale.exists()


def test_incomplete_transaction_defers_cleanup_without_deleting(tmp_path, clock):
    root = tmp_path / "runtime"
    paths = [_release(root, f"v{i}", 9 * 86400 + i) for i in range(5)]
    _pointers(root, paths[0], paths[1])
    t = root / "transactions" / "still-applying"
    t.mkdir(parents=True)
    (t / "transaction.json").write_text(json.dumps({
        "schema": updater.TRANSACTION_SCHEMA,
        "state": "applying",
    }), encoding="utf-8")
    result = updater._gc(root, 2)
    assert result["deferred"] == 1 and result["removed"] == 0
    assert all(p.exists() for p in paths)
    assert not (root / updater.RETENTION_STATE_FILE).exists()


def test_invalid_metadata_is_protected_and_symlink_not_followed(tmp_path, clock):
    root = tmp_path / "runtime"
    a = _release(root, "active", 5 * 86400)
    b = _release(root, "previous", 6 * 86400)
    malformed = _release(root, "unknown-date", 7 * 86400)
    (malformed / ".cifereca-install.json").write_text("{invalid", encoding="utf-8")
    stale = _release(root, "stale", 8 * 86400)
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "releases" / "external").symlink_to(outside, target_is_directory=True)
    _pointers(root, a, b)
    result = updater._gc(root, 2)
    assert result["retained"] == 3
    assert a.exists() and b.exists() and malformed.exists()
    assert not stale.exists()
    assert outside.exists() and (root / "releases" / "external").is_symlink()


def test_legacy_missing_install_receipt_keeps_ownership_unqualified_release(tmp_path, clock):
    root = tmp_path / "runtime"
    a = _release(root, "active", 72 * 3600, metadata=False)
    b = _release(root, "previous", 96 * 3600, metadata=False)
    stale = _release(root, "very-old", 120 * 3600, metadata=False)
    _pointers(root, a, b)
    # mtime can establish a minimum-retention ordering but can never prove
    # that an arbitrary historical directory belongs to this updater.
    result = updater._gc(root, 2)
    assert result["removed"] == 0 and stale.exists()
    assert a.exists() and b.exists()


def test_status_exposes_policy_and_last_cleanup_without_hashing_files(tmp_path, clock, monkeypatch):
    root = tmp_path / "runtime"
    monkeypatch.setenv("CIFERECA_INSTALL_ROOT", str(root))
    monkeypatch.setenv("CIFERECA_BIN_DIR", str(tmp_path / "bin"))
    a = _release(root, "a", 1)
    b = _release(root, "b", 100)
    _pointers(root, a, b)
    updater._gc(root, 2)
    row = updater.status()["release_retention"]
    assert row["max_age_hours"] == 24
    assert row["minimum_generations"] == 2
    assert row["retained_generations"] == 2
    assert row["last_cleanup_at"]
    assert row["last_removed"] == 0
    assert row["effective_minimum_generations"] == 2
