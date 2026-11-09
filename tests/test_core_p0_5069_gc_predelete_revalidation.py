"""P0 #5069: a new committed rollback reference blocks the GC sweep."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os

import pytest

from cifereca import updater
from cifereca import updater_retention_inventory as inventory

NOW = 2_000_000_000.0


def _release(root, name, days):
    path = root / "releases" / name
    path.mkdir(parents=True)
    (path / ".cifereca-install.json").write_text(json.dumps({
        "version": name,
        "installed_at": datetime.fromtimestamp(
            NOW - days * 86400, timezone.utc,
        ).isoformat(),
    }))
    os.utime(path, (NOW - days * 86400, NOW - days * 86400))
    return path


def _runtime(tmp_path, monkeypatch):
    monkeypatch.setattr(updater.time, "time", lambda: NOW)
    root = tmp_path / "runtime"
    current = _release(root, "current", 2)
    previous = _release(root, "previous", 3)
    stale = _release(root, "stale", 8)
    updater._replace_symlink(root / "current", current)
    updater._replace_symlink(root / "previous", previous)
    (root / "transactions").mkdir()
    return root, stale


def _transaction(root, release):
    folder = root / "transactions" / "late-transaction"
    folder.mkdir()
    (folder / "transaction.json").write_text(json.dumps({
        "schema": updater.TRANSACTION_SCHEMA,
        "id": folder.name,
        "created_at": datetime.fromtimestamp(
            NOW - 60, timezone.utc,
        ).strftime("%Y%m%dT%H%M%SZ"),
        "state": "applied",
        "core": {
            "before_current": None,
            "before_previous": {"path": str(release)},
            "after": None,
        },
        "packages": {},
    }))


def test_new_retained_transaction_before_gc_prevents_deletion(tmp_path, monkeypatch):
    root, stale = _runtime(tmp_path, monkeypatch)
    original = inventory.inspect_core_transaction_history
    calls = 0

    def mutate_between_plan_and_delete(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            _transaction(root, stale)
        return original(*args, **kwargs)

    monkeypatch.setattr(inventory, "inspect_core_transaction_history",
                        mutate_between_plan_and_delete)
    with pytest.raises(updater.UpdateError, match="désormais protégée"):
        updater._gc(root, 2)
    assert calls == 2
    assert stale.is_dir()


def test_unmodified_inventory_allows_core_gc(tmp_path, monkeypatch):
    root, stale = _runtime(tmp_path, monkeypatch)
    report = updater._gc(root, 2)
    assert report["removed"] == 1
    assert not stale.exists()
