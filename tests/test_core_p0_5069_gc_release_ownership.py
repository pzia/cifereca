"""P0 #5069: real Core GC must apply the same receipt ownership gate as inventory."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os

from cifereca import updater


NOW = 2_000_000_000.0


def _release(root, name, age, marker):
    release = root / "releases" / name
    release.mkdir(parents=True)
    if marker is not None:
        (release / ".cifereca-install.json").write_text(json.dumps(marker))
    os.utime(release, (NOW - age, NOW - age))
    return release


def test_owned_old_release_removed_but_unknown_and_corrupted_peers_retained(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(updater.time, "time", lambda: NOW)
    root = tmp_path / "runtime"
    valid = {"version": "1.0"}
    current = _release(root, "current", 2 * 86400, valid)
    previous = _release(root, "previous", 3 * 86400, valid)
    eligible = _release(root, "eligible", 5 * 86400, valid)
    legacy = _release(root, "legacy", 6 * 86400, None)
    corrupt = _release(root, "corrupt", 7 * 86400, {"version": ""})
    updater._replace_symlink(root / "current", current)
    updater._replace_symlink(root / "previous", previous)
    result = updater._gc(root, 2)
    assert result["removed"] == 1
    assert not eligible.exists()
    assert current.is_dir() and previous.is_dir()
    assert legacy.is_dir() and corrupt.is_dir()


def test_release_receipt_symlinks_and_hardlinks_are_not_physical_gc_authority(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(updater.time, "time", lambda: NOW)
    root = tmp_path / "runtime"
    current = _release(root, "current", 2 * 86400, {"version": "current"})
    previous = _release(root, "previous", 3 * 86400, {"version": "previous"})
    updater._replace_symlink(root / "current", current)
    updater._replace_symlink(root / "previous", previous)
    private = tmp_path / "private-receipt.json"
    private.write_text('{"version":"private"}')
    link = _release(root, "symlinked", 5 * 86400, None)
    (link / ".cifereca-install.json").symlink_to(private)
    alias = _release(root, "hardlinked", 6 * 86400, None)
    os.link(private, alias / ".cifereca-install.json")
    result = updater._gc(root, 2)
    assert result["removed"] == 0
    assert link.exists() and alias.exists()
    assert private.read_text() == '{"version":"private"}'


def test_receipt_with_invalid_timestamp_is_not_gc_owned(tmp_path, monkeypatch):
    monkeypatch.setattr(updater.time, "time", lambda: NOW)
    root = tmp_path / "runtime"
    current = _release(root, "current", 2 * 86400, {"version": "current"})
    previous = _release(root, "previous", 3 * 86400, {"version": "previous"})
    bad = _release(root, "invalid", 8 * 86400, {
        "version": "1.0", "installed_at": "not-an-iso-date",
    })
    updater._replace_symlink(root / "current", current)
    updater._replace_symlink(root / "previous", previous)
    result = updater._gc(root, 2)
    assert result["removed"] == 0
    assert bad.is_dir()
