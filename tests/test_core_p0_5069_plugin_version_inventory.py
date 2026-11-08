"""P0 #5069 B3: private read-only plug-in version inventory tests."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os

from cifereca.updater_retention_plugin_inventory import inspect_plugin_version_history


NOW = datetime(2026, 11, 8, 19, tzinfo=timezone.utc).timestamp()


def _version(root, package, version, age, *, owned=True):
    location = root / package / version
    location.mkdir(parents=True)
    (location / "cifereca-plugin.json").write_text(json.dumps({
        "package": {"id": package, "version": version},
    }), encoding="utf-8")
    if owned:
        (location / ".cifereca-install.json").write_text(json.dumps({
            "sha256": "a" * 64,
            "installed_at": datetime.fromtimestamp(NOW - age, timezone.utc).isoformat(),
        }), encoding="utf-8")
    return location


def _plan(root):
    return inspect_plugin_version_history(root, now=NOW)


def test_seven_versions_in_a_day_are_all_retained_without_cap(tmp_path):
    store = tmp_path / "packages"
    for i in range(7):
        _version(store, "org.example", f"1.0.{i}", 10 * i)
    before = {p: p.stat().st_mtime_ns for p in store.rglob("*")}
    report = _plan(store)
    assert report["safe_to_apply"] is False
    assert report["plan"]["total"] == 7
    assert report["plan"]["candidates"] == 0
    assert "plugin_versions" in report["coverage"]
    assert before == {p: p.stat().st_mtime_ns for p in store.rglob("*")}


def test_two_old_versions_kept_for_each_package(tmp_path):
    store = tmp_path / "packages"
    for package in ("org.one", "org.two"):
        for i in range(7):
            _version(store, package, f"1.0.{i}", 48 * 3600 + i * 100)
    p = _plan(store)["plan"]
    assert p["total"] == 14
    assert p["candidates"] == 10
    assert sum(r["decision"] == "retain" for r in p["objects"]) == 4
    assert p["safe_to_apply"] is False


def test_active_and_previous_pins_are_preserved_beyond_two(tmp_path):
    store = tmp_path / "packages"
    for i in range(6):
        _version(store, "org.test", f"1.0.{i}", 50 * 3600 + i * 100)
    (store / "org.test" / "current.json").write_text(
        json.dumps({"active": "1.0.5", "previous": "1.0.4"}),
        encoding="utf-8",
    )
    rows = _plan(store)["plan"]["objects"]
    assert sum("active" in x["reasons"] for x in rows) == 1
    assert sum("pinned" in x["reasons"] for x in rows) == 1
    assert sum(x["decision"] == "retain" for x in rows) == 4


def test_missing_install_receipt_is_unqualified_and_conservatively_retained(tmp_path):
    store = tmp_path / "packages"
    _version(store, "org.test", "1.0.0", 55 * 3600, owned=False)
    report = _plan(store)
    assert "plugin_version_ownership_unqualified" in report["blockers"]
    assert "unowned" in report["plan"]["objects"][0]["reasons"]
    assert report["plan"]["candidates"] == 0


def test_bad_manifest_identity_never_proves_ownership(tmp_path):
    store = tmp_path / "packages"
    location = _version(store, "org.expected", "1.0", 56 * 3600)
    (location / "cifereca-plugin.json").write_text(
        '{"package": {"id":"org.other","version":"1.0"}}',
        encoding="utf-8",
    )
    result = _plan(store)
    assert "plugin_version_ownership_unqualified" in result["blockers"]
    assert result["plan"]["objects"][0]["decision"] == "retain"


def test_symlinked_package_or_version_is_never_read(tmp_path):
    store = tmp_path / "packages"
    store.mkdir()
    outside = tmp_path / "credentials"
    outside.mkdir()
    (outside / "secret").write_text("private value", encoding="utf-8")
    (store / "fake.package").symlink_to(outside, target_is_directory=True)
    safe = store / "org.safe"
    safe.mkdir()
    (safe / "1.0").symlink_to(outside, target_is_directory=True)
    report = _plan(store)
    assert "unsafe_plugin_package" in report["blockers"]
    assert "unsafe_plugin_version" in report["blockers"]
    assert report["plan"]["total"] == 0
    assert "credentials" not in json.dumps(report)
    assert "private value" not in json.dumps(report)


def test_invalid_pointer_pins_all_versions(tmp_path):
    store = tmp_path / "packages"
    for i in range(5):
        _version(store, "org.test", f"1.0.{i}", 50 * 3600 + 100 * i)
    (store / "org.test" / "current.json").write_text("not json", encoding="utf-8")
    report = _plan(store)
    assert "invalid_plugin_pointer" in report["blockers"]
    assert report["plan"]["candidates"] == 0


def test_pointer_missing_version_is_blocker(tmp_path):
    store = tmp_path / "packages"
    for i in range(4):
        _version(store, "org.test", f"1.0.{i}", 50 * 3600 + i * 100)
    (store / "org.test" / "current.json").write_text(json.dumps({
        "active": "10.1.unknown", "previous": "1.0.1",
    }), encoding="utf-8")
    result = _plan(store)
    assert "unresolved_plugin_pointer" in result["blockers"]
    assert result["safe_to_apply"] is False


def test_symlinked_active_pointer_is_blocker_and_all_pinned(tmp_path):
    store = tmp_path / "packages"
    for i in range(4):
        _version(store, "org.test", f"1.0.{i}", 50 * 3600 + i * 100)
    outside = tmp_path / "sensitive"
    outside.write_text("SECRET", encoding="utf-8")
    (store / "org.test" / "current.json").symlink_to(outside)
    report = _plan(store)
    assert "unsafe_plugin_pointer" in report["blockers"]
    assert report["plan"]["candidates"] == 0
    assert "SECRET" not in json.dumps(report)
