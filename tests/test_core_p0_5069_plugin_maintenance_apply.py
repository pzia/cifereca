"""P0 #5069: executable plugin retention uses the exact installed-package unit."""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json

import pytest

from cifereca import updater
from cifereca.updater_plugin_maintenance import plugin_history_maintenance


NOW = 2_000_000_000.0


def _iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()


def _fixture(tmp_path, *, old: bool = True):
    root = tmp_path / "runtime"
    packages = tmp_path / "data" / "plugins" / "packages"
    package = packages / "example.readers"
    package.mkdir(parents=True)
    (package / "current.json").write_text(json.dumps({
        "active": "v0", "previous": "v1",
    }))
    versions = []
    for i in range(7):
        name = f"v{i}"
        version = package / name
        version.mkdir()
        (version / ".cifereca-install.json").write_text(json.dumps({
            "sha256": "a" * 64,
            "installed_at": _iso(NOW - (3 * 86400 if old else 60) - i * 60),
        }))
        (version / "cifereca-plugin.json").write_text(json.dumps({
            "package": {"id": "example.readers", "version": name},
        }))
        (version / "module.py").write_text(f"VERSION = {i}\n")
        versions.append(version)
    return root, packages, versions


def _trusted_test_leases(monkeypatch):
    monkeypatch.setattr(updater, "_trusted_package_lease",
                        lambda: __import__("contextlib").nullcontext())


def test_seven_recent_versions_all_preserved(tmp_path):
    root, packages, versions = _fixture(tmp_path, old=False)
    result = plugin_history_maintenance(root, packages, now=NOW)
    assert result["candidate_versions"] == 0
    assert result["safe_to_apply"] is False
    assert all(version.exists() for version in versions)


def test_seven_old_versions_prune_five_and_keep_active_previous(tmp_path, monkeypatch):
    root, packages, versions = _fixture(tmp_path)
    _trusted_test_leases(monkeypatch)
    plan = plugin_history_maintenance(root, packages, now=NOW)
    assert plan["candidate_versions"] == 5
    result = plugin_history_maintenance(
        root, packages, now=NOW, apply=True,
        acknowledge=plan["acknowledgement_sha256"],
    )
    assert result["removed"] == 5
    assert all(version.exists() for version in versions[:2])
    assert all(not version.exists() for version in versions[2:])
    audit = packages.parent / ".cifereca-plugin-gc"
    assert len(list(audit.glob("*.json"))) == 5


def test_recent_transaction_pins_exact_old_version(tmp_path, monkeypatch):
    root, packages, versions = _fixture(tmp_path)
    transactions = root / "transactions"
    folder = transactions / "tx-pinned"
    folder.mkdir(parents=True)
    (folder / "plugins").mkdir()
    (folder / "transaction.json").write_text(json.dumps({
        "schema": updater.TRANSACTION_SCHEMA,
        "id": folder.name,
        "created_at": datetime.fromtimestamp(
            NOW - 60, timezone.utc,
        ).strftime("%Y%m%dT%H%M%SZ"),
        "state": "applied",
        "core": {"before_current": None, "before_previous": None, "after": None},
        "packages": {
            "example.readers": {
                "before": {"exists": True, "versions": ["v4"]},
                "after": {"exists": True, "versions": ["v0"]},
            },
        },
    }))
    _trusted_test_leases(monkeypatch)
    plan = plugin_history_maintenance(root, packages, now=NOW)
    assert plan["candidate_versions"] == 4
    applied = plugin_history_maintenance(
        root, packages, now=NOW, apply=True,
        acknowledge=plan["acknowledgement_sha256"],
    )
    assert applied["removed"] == 4
    assert versions[4].is_dir()
    assert all(version.is_dir() for version in versions[:2])


def test_wrong_acknowledgement_never_changes_plugin_tree(tmp_path, monkeypatch):
    root, packages, versions = _fixture(tmp_path)
    _trusted_test_leases(monkeypatch)
    with pytest.raises(ValueError, match="plan_changed"):
        plugin_history_maintenance(
            root, packages, now=NOW, apply=True,
            acknowledge="f" * 64,
        )
    assert all(version.is_dir() for version in versions)
    assert not (packages.parent / ".cifereca-plugin-gc").exists()


def test_unqualified_plugin_version_is_never_deleted(tmp_path):
    root, packages, versions = _fixture(tmp_path)
    (versions[6] / ".cifereca-install.json").unlink()
    with pytest.raises(ValueError, match="inventory_unqualified"):
        plugin_history_maintenance(root, packages, now=NOW)
    assert all(version.is_dir() for version in versions)
