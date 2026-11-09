"""P0 #5069: the physical Core GC must respect retained transaction roots."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os

from cifereca import updater


NOW = 2_000_000_000.0


def _release(root, name, age):
    target = root / "releases" / name
    target.mkdir(parents=True)
    when = datetime.fromtimestamp(NOW - age, timezone.utc).isoformat()
    (target / ".cifereca-install.json").write_text(
        json.dumps({"version": name, "installed_at": when}), encoding="utf-8",
    )
    os.utime(target, (NOW - age, NOW - age))
    return target


def _transaction(root, name, *, age, before_previous, before_current=None):
    folder = root / "transactions" / name
    folder.mkdir(parents=True)
    stamp = datetime.fromtimestamp(NOW - age, timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    journal = {
        "schema": updater.TRANSACTION_SCHEMA,
        "id": name, "created_at": stamp, "state": "applied",
        "core": {
            "before_current": {"path": str(before_current)} if before_current else None,
            "before_previous": {"path": str(before_previous)} if before_previous else None,
            "after": None,
        },
        "packages": {},
    }
    (folder / "transaction.json").write_text(json.dumps(journal), encoding="utf-8")
    return folder


def test_old_rollback_release_pinned_by_recent_transaction_survives_gc(tmp_path, monkeypatch):
    root = tmp_path / "runtime"
    monkeypatch.setattr(updater.time, "time", lambda: NOW)
    current = _release(root, "current", 2 * 86400)
    previous = _release(root, "previous", 3 * 86400)
    ancient = _release(root, "ancient", 8 * 86400)
    stale = _release(root, "stale", 9 * 86400)
    updater._replace_symlink(root / "current", current)
    updater._replace_symlink(root / "previous", previous)
    _transaction(root, "tx-recent", age=3600, before_previous=ancient)
    report = updater._gc(root, 2)
    assert report["removed"] == 1
    assert ancient.is_dir()
    assert not stale.exists()
    assert current.is_dir() and previous.is_dir()


def test_seven_recent_transactions_keep_seven_old_rollback_generations(tmp_path, monkeypatch):
    root = tmp_path / "runtime"
    monkeypatch.setattr(updater.time, "time", lambda: NOW)
    retained = [_release(root, f"rollback-{i}", 5 * 86400 + i * 3600)
                for i in range(7)]
    orphan = _release(root, "orphan", 20 * 86400)
    updater._replace_symlink(root / "current", retained[0])
    updater._replace_symlink(root / "previous", retained[1])
    for index, release in enumerate(retained):
        _transaction(root, f"tx-{index}", age=3600 + index * 60,
                     before_previous=release)
    result = updater._gc(root, 2)
    assert result["removed"] == 1
    assert not orphan.exists()
    assert all(release.is_dir() for release in retained)


def test_missing_retained_transaction_reference_defers_all_gc(tmp_path, monkeypatch):
    root = tmp_path / "runtime"
    monkeypatch.setattr(updater.time, "time", lambda: NOW)
    releases = [_release(root, f"v{i}", 5 * 86400 + i * 3600) for i in range(4)]
    updater._replace_symlink(root / "current", releases[0])
    updater._replace_symlink(root / "previous", releases[1])
    _transaction(root, "tx-recent", age=100, before_previous=root / "releases" / "lost")
    result = updater._gc(root, 2)
    assert result["removed"] == 0 and result["deferred"] == 1
    assert all(release.is_dir() for release in releases)


def test_two_old_transactions_pin_only_their_required_releases(tmp_path, monkeypatch):
    root = tmp_path / "runtime"
    monkeypatch.setattr(updater.time, "time", lambda: NOW)
    current = _release(root, "current", 2 * 86400)
    previous = _release(root, "previous", 3 * 86400)
    historical = [_release(root, f"history-{idx}", 8 * 86400 + idx * 3600)
                  for idx in range(7)]
    updater._replace_symlink(root / "current", current)
    updater._replace_symlink(root / "previous", previous)
    for index, release in enumerate(historical):
        _transaction(root, f"tx-{index}", age=3 * 86400 + index * 60,
                     before_previous=release)
    result = updater._gc(root, 2)
    assert result["removed"] == 5
    # Two most recent transactions (index 0, 1), not the oldest five.
    assert historical[0].exists() and historical[1].exists()
    assert all(not item.exists() for item in historical[2:])
