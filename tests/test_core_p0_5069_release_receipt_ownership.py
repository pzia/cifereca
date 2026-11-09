"""P0 #5069 B11: untrusted Core release receipts cannot become GC ownership."""
from __future__ import annotations

import json
import os

import pytest

from cifereca.updater_retention_inventory import inspect_core_transaction_history


NOW = 1_000_000.0


def _release(root, name, receipt, *, time=NOW - 200_000):
    target = root / "releases" / name
    target.mkdir(parents=True)
    if receipt is not None:
        marker = target / ".cifereca-install.json"
        if isinstance(receipt, dict):
            marker.write_text(json.dumps(receipt), encoding="utf-8")
        else:
            marker.write_text(receipt, encoding="utf-8")
    os.utime(target, (time, time))
    return target


def _decision(report, index):
    rows = report["plan"]["objects"]
    return rows[index]


def test_legacy_and_modern_receipts_remain_eligible_only_as_non_apply_candidates(tmp_path):
    root = tmp_path / "runtime"
    for index in range(7):
        receipt = {"version": "1.0"}
        if index == 3:
            receipt["installed_at"] = "1970-01-10T00:00:00+00:00"
        _release(root, f"v{index}", receipt, time=NOW - 200_000 - index * 100)
    result = inspect_core_transaction_history(root, now=NOW)
    assert result["plan"]["total"] == 7
    assert result["plan"]["candidates"] == 5
    assert result["safe_to_apply"] is False
    assert "release_ownership_unqualified" not in result["blockers"]


@pytest.mark.parametrize("bad_receipt", [
    None,
    "",
    "PRIVATE-INVALID-JSON",
    "[]",
    {},
    {"version": 42},
    {"version": "  "},
    {"version": "1", "installed_at": "yesterday"},
    {"version": "1", "installed_at": "2026-11-09T10:00:00"},
    {"version": "1", "installed_at": True},
    "x" * (64 * 1024 + 1),
])
def test_unqualified_receipts_are_retained_and_never_trigger_deletion(tmp_path, bad_receipt):
    root = tmp_path / "runtime"
    for idx in range(4):
        _release(root, f"good-{idx}", {"version": "1"}, time=NOW - 200_000 - idx)
    bad = _release(root, "unqualified", bad_receipt, time=NOW - 300_000)
    before = sorted(str(path) for path in root.rglob("*"))
    report = inspect_core_transaction_history(root, now=NOW)
    assert "release_ownership_unqualified" in report["blockers"]
    assert report["plan"]["total"] == 5
    assert report["plan"]["candidates"] == 2
    assert any(
        row["decision"] == "retain" and "unowned" in row["reasons"]
        and "unknown_timestamp" in row["reasons"]
        for row in report["plan"]["objects"]
    )
    assert bad.is_dir()
    assert sorted(str(path) for path in root.rglob("*")) == before
    assert report["safe_to_apply"] is False


@pytest.mark.parametrize("kind", ["symlink", "hardlink"])
def test_private_receipt_alias_never_qualifies_updater_ownership(tmp_path, kind):
    root = tmp_path / "runtime"
    source = tmp_path / "private.json"
    source.write_text('{"version":"1"}', encoding="utf-8")
    directory = _release(root, "suspect", None)
    marker = directory / ".cifereca-install.json"
    if kind == "symlink":
        marker.symlink_to(source)
    else:
        os.link(source, marker)
    report = inspect_core_transaction_history(root, now=NOW)
    assert report["plan"]["total"] == 1
    assert report["plan"]["candidates"] == 0
    assert "release_ownership_unqualified" in report["blockers"]
    assert "PRIVATE" not in json.dumps(report)
    assert source.read_text(encoding="utf-8") == '{"version":"1"}'
    assert report["safe_to_apply"] is False


def test_receipt_is_not_followed_when_release_entry_is_symlink(tmp_path):
    root = tmp_path / "runtime"
    private = tmp_path / "private"
    private.mkdir()
    (private / ".cifereca-install.json").write_text(
        '{"version":"PRIVATE-SECRET"}', encoding="utf-8",
    )
    releases = root / "releases"
    releases.mkdir(parents=True)
    (releases / "fake").symlink_to(private, target_is_directory=True)
    report = inspect_core_transaction_history(root, now=NOW)
    assert report["plan"]["total"] == 0
    assert "unsafe_release_entry" in report["blockers"]
    assert "PRIVATE-SECRET" not in json.dumps(report)
    assert report["safe_to_apply"] is False
