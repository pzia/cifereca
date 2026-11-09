"""P0 #5069: the destructive Core GC must honor inventory graph blockers."""
from __future__ import annotations

import json
import os

import pytest

from cifereca import updater


NOW = 2_000_000_000.0


def _release(root, name, age):
    path = root / "releases" / name
    path.mkdir(parents=True)
    (path / ".cifereca-install.json").write_text(json.dumps({"version": name}))
    os.utime(path, (NOW - age, NOW - age))
    return path


def _runtime(tmp_path, monkeypatch):
    monkeypatch.setattr(updater.time, "time", lambda: NOW)
    root = tmp_path / "runtime"
    current = _release(root, "current", 2 * 86400)
    previous = _release(root, "previous", 3 * 86400)
    stale = _release(root, "stale", 6 * 86400)
    updater._replace_symlink(root / "current", current)
    updater._replace_symlink(root / "previous", previous)
    (root / "transactions").mkdir()
    return root, stale


def test_broken_release_transaction_pointer_defers_physical_gc(tmp_path, monkeypatch):
    root, stale = _runtime(tmp_path, monkeypatch)
    (stale / updater.TRANSACTION_FILE).write_text('{"schema": 1, "id": "../escaped"}')
    result = updater._gc(root, 2)
    assert result["removed"] == 0
    assert result["deferred"] == 1
    assert stale.is_dir()


def test_unqualified_core_pointer_defers_physical_gc(tmp_path, monkeypatch):
    root, stale = _runtime(tmp_path, monkeypatch)
    (root / "previous").unlink()
    (root / "previous").symlink_to("releases/missing")
    # Existing Core pointer validation rejects earlier than inventory GC.
    with pytest.raises(updater.UpdateError, match="référence Core non résolue"):
        updater._gc(root, 2)
    assert stale.is_dir()


def test_without_graph_blocker_old_owned_release_is_eligible(tmp_path, monkeypatch):
    root, stale = _runtime(tmp_path, monkeypatch)
    result = updater._gc(root, 2)
    assert result["removed"] == 1
    assert result["deferred"] == 0
    assert not stale.exists()
