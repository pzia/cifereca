"""P0 #5069 B3: synthetic, offline plugin version inventory safety proofs."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

from cifereca.updater_retention_plugins import (
    inspect_plugin_version_history, inspect_updater_retention_readonly,
)


NOW = 2_000_000_000.0
PKG = "org.example.private-provider"


def _version(root: Path, version: str, *, age: int, name: str = PKG,
             receipt: bool = True, timestamp: str | None = None) -> Path:
    folder = root / name / version
    folder.mkdir(parents=True)
    (folder / "cifereca-plugin.json").write_text(json.dumps({
        "manifest": 1, "package": {"id": name, "version": version},
        "plugins": [],
    }), encoding="utf-8")
    if receipt:
        (folder / ".cifereca-install.json").write_text(json.dumps({
            "sha256": "a" * 64,
            "installed_at": timestamp or datetime.fromtimestamp(
                NOW - age, tz=timezone.utc,
            ).isoformat(),
        }), encoding="utf-8")
    return folder


def _pointer(root: Path, active: str, previous: str | None) -> None:
    (root / PKG / "current.json").write_text(
        json.dumps({"active": active, "previous": previous}), encoding="utf-8",
    )


def _decisions(report):
    return report["plan"]["objects"]


def test_seven_versions_inside_24h_all_retained(tmp_path):
    for number in range(1, 8):
        _version(tmp_path, f"1.0.{number}", age=number * 3600)
    _pointer(tmp_path, "1.0.7", "1.0.6")
    report = inspect_plugin_version_history(tmp_path, now=NOW)
    assert report["safe_to_apply"] is False
    assert report["coverage"] == ["plugin_versions"]
    assert "plugin_pointer_unreadable" not in report["blockers"]
    assert report["plan"]["candidates"] == 0
    assert all(row["decision"] == "retain" for row in _decisions(report))


def test_minimum_two_versions_plus_active_and_previous_pins(tmp_path):
    for number in range(1, 6):
        _version(tmp_path, f"1.0.{number}", age=(6-number)*86400)
    _pointer(tmp_path, "1.0.1", "1.0.2")
    report = inspect_plugin_version_history(tmp_path, now=NOW)
    assert report["safe_to_apply"] is False
    # 1.0.5 and 1.0.4 are the two newest; 1.0.1 and 1.0.2
    # are independently pinned, leaving only 1.0.3 as a candidate.
    assert report["plan"]["candidates"] == 1
    assert sum("minimum_generations" in row["reasons"] for row in _decisions(report)) == 2
    assert sum("pinned" in row["reasons"] for row in _decisions(report)) == 2


def test_manifest_identity_mismatch_fails_ownership(tmp_path):
    folder = _version(tmp_path, "1.0.0", age=10*86400)
    marker = folder / "cifereca-plugin.json"
    bad = json.loads(marker.read_text(encoding="utf-8"))
    bad["package"]["id"] = "org.other"
    marker.write_text(json.dumps(bad), encoding="utf-8")
    _pointer(tmp_path, "1.0.0", None)
    report = inspect_plugin_version_history(tmp_path, now=NOW)
    assert "plugin_version_ownership_unqualified" in report["blockers"]
    assert "unowned" in _decisions(report)[0]["reasons"]
    assert report["plan"]["candidates"] == 0


def test_symlink_metadata_refused_without_reading_external(tmp_path):
    folder = _version(tmp_path, "1.0.0", age=9*86400)
    outside = tmp_path.parent / ("outside-" + tmp_path.name)
    outside.write_text('{"sha256": "' + "a"*64 + '"}', encoding="utf-8")
    marker = folder / ".cifereca-install.json"
    marker.unlink()
    marker.symlink_to(outside)
    _pointer(tmp_path, "1.0.0", None)
    report = inspect_plugin_version_history(tmp_path, now=NOW)
    assert "plugin_version_ownership_unqualified" in report["blockers"]
    assert outside.read_text(encoding="utf-8").startswith('{"sha256"')


def test_bad_pointer_never_releases_versions(tmp_path):
    for number in range(1, 5):
        _version(tmp_path, f"1.0.{number}", age=number*86400)
    (tmp_path / PKG / "current.json").write_text("{", encoding="utf-8")
    report = inspect_plugin_version_history(tmp_path, now=NOW)
    assert "plugin_pointer_unreadable" in report["blockers"]
    assert report["plan"]["candidates"] == 0
    assert all("pinned" in row["reasons"] for row in _decisions(report))


def test_missing_previous_pointer_is_reported_not_assumed_disposable(tmp_path):
    _version(tmp_path, "1.0.0", age=30*86400)
    _version(tmp_path, "1.0.1", age=29*86400)
    _pointer(tmp_path, "1.0.1", "0.9.0")
    report = inspect_plugin_version_history(tmp_path, now=NOW)
    assert "unresolved_plugin_pointer" in report["blockers"]
    assert report["safe_to_apply"] is False


def test_hidden_staging_and_symlinked_version_are_not_candidates(tmp_path):
    _version(tmp_path, "1.0.0", age=10*86400)
    _pointer(tmp_path, "1.0.0", None)
    hidden = tmp_path / PKG / ".1.0.1.replace-123"
    hidden.mkdir()
    (tmp_path / PKG / "2.0.0").symlink_to(hidden, target_is_directory=True)
    report = inspect_plugin_version_history(tmp_path, now=NOW)
    assert "unqualified_plugin_staging" in report["blockers"]
    assert "unsafe_plugin_version_entry" in report["blockers"]
    assert report["plan"]["candidates"] == 0


def test_user_paths_never_in_public_projection(tmp_path):
    _version(tmp_path, "1.0.0", age=30*86400)
    _pointer(tmp_path, "1.0.0", None)
    report = inspect_plugin_version_history(tmp_path, now=NOW)
    dumped = json.dumps(report)
    assert PKG not in dumped
    assert str(tmp_path) not in dumped
    assert "cross_store_references_unqualified" in report["blockers"]


def test_root_symlink_rejected_without_traversal(tmp_path):
    source = tmp_path / "real"
    source.mkdir()
    shortcut = tmp_path / "shortcut"
    shortcut.symlink_to(source, target_is_directory=True)
    report = inspect_plugin_version_history(shortcut, now=NOW)
    assert report["plan"] is None
    assert report["blockers"] == ["unsafe_plugin_store"]


def test_aggregate_keeps_store_plans_separate_and_purge_forbidden(tmp_path):
    runtime = tmp_path / "runtime"
    plugins = tmp_path / "plugins"
    _version(plugins, "1.0.0", age=7*86400)
    _pointer(plugins, "1.0.0", None)
    report = inspect_updater_retention_readonly(runtime, plugins, now=NOW)
    assert report["safe_to_apply"] is False
    assert "plugin_versions" in report["coverage"]
    assert "cross_store_reference_proofs" in report["pending_stores"]
    assert report["plans"]["core_transactions"] is not None
    assert report["plans"]["plugin_versions"] is not None
    assert "plans" in report and "plan" not in report
