#!/usr/bin/env python3
"""Collect actual pytest item IDs and classify only explicitly supported contracts.

#5376: opt-in offline inventory, NOT a test runner nor a PR-wide gate.
A collecting pytest plugin records items without running the test functions.
Unknown business ownership and level remain visible as "unclassified".
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import os
import re
import tempfile
import subprocess
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "docs/test-classification-manifest.json"
OWNERS = {"core", "coreui", "unclassified"}
BUSINESS = {
    "acquisition", "synchronization", "catalogue_search", "accounts_auth",
    "loans", "storage_library", "diagnostics", "plugin_contracts",
    "ui", "ci_quality", "drm", "other", "unclassified",
}
LEVELS = {"unit", "contract", "integration", "e2e", "unclassified"}
_PATH_RE = re.compile(r"^(tests|src/plugins/[a-zA-Z0-9_-]+/tests)/.+\.py$")
_SUITE_RE = re.compile(r"^(?:tests|src/plugins/[a-zA-Z0-9_-]+/tests)$")
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def _item_path(nodeid: str) -> str:
    return nodeid.split("::", 1)[0]


def validate_manifest(value: object) -> dict[str, dict[str, str]]:
    """Reject duplicate paths, unsupported values, implicit wildcard owners."""
    if not isinstance(value, dict) or value.get("schema") != "cifereca.test-classification/1":
        raise ValueError("invalid classification schema")
    records = value.get("files")
    if not isinstance(records, list):
        raise ValueError("files must be a list")
    results: dict[str, dict[str, str]] = {}
    for entry in records:
        if not isinstance(entry, dict) or set(entry) != {
            "path", "owner_architecture", "business_object", "test_level",
        }:
            raise ValueError("malformed classification entry")
        path = entry["path"]
        if (not isinstance(path, str) or not _PATH_RE.fullmatch(path)
                or ".." in Path(path).parts or path in results):
            raise ValueError("invalid or duplicate classification path")
        owner = entry["owner_architecture"]
        if owner not in OWNERS and not (
            isinstance(owner, str) and owner.startswith("plugin:")
            and re.fullmatch(r"plugin:[a-zA-Z0-9_-]+", owner)
        ):
            raise ValueError("invalid owner")
        plugin = path.split("/")[2] if path.startswith("src/plugins/") else None
        if (plugin and owner != "plugin:" + plugin) or (not plugin and owner.startswith("plugin:")):
            raise ValueError("owner does not match test package")
        if entry["business_object"] not in BUSINESS or entry["test_level"] not in LEVELS:
            raise ValueError("invalid object or test level")
        results[path] = dict(entry)
    return results


def classify(nodeid: str, mapping: dict[str, dict[str, str]]) -> dict[str, str]:
    path = _item_path(nodeid)
    entry = mapping.get(path)
    if entry is not None:
        return {key: entry[key] for key in (
            "owner_architecture", "business_object", "test_level",
        )}
    # Plugin ownership is a fact of package location; category, business
    # object and test level cannot be inferred from the filename alone.
    parts = path.split("/")
    owner = "plugin:" + parts[2] if (
        len(parts) >= 5 and parts[:2] == ["src", "plugins"]
        and parts[3] == "tests"
    ) else "unclassified"
    return {
        "owner_architecture": owner,
        "business_object": "unclassified",
        "test_level": "unclassified",
    }


class CollectionCapture:
    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []

    def pytest_collection_finish(self, session: Any) -> None:
        for item in session.items:
            names = set(item.keywords)
            self.items.append({
                "nodeid": item.nodeid,
                "skip_marked": "skip" in names or "skipif" in names,
                "xfail_marked": "xfail" in names,
            })


def make_report(
    items: list[dict[str, Any]], manifest: dict[str, dict[str, str]],
    *, suite: str, source_sha: str | None = None,
) -> dict[str, Any]:
    classified = [
        {"nodeid": record["nodeid"], **classify(record["nodeid"], manifest),
         "skip_marked": bool(record.get("skip_marked")),
         "xfail_marked": bool(record.get("xfail_marked"))}
        for record in items
    ]
    classified.sort(key=lambda record: record["nodeid"])
    if len({item["nodeid"] for item in classified}) != len(classified):
        raise ValueError("duplicate collected pytest nodeid")
    matrix = Counter(
        (row["owner_architecture"], row["business_object"], row["test_level"])
        for row in classified
    )
    return {
        "schema": "cifereca.pytest-collected-inventory/1",
        "suite": suite,
        "source_sha": source_sha,
        "count_collected": len(classified),
        "count_skip_marked": sum(row["skip_marked"] for row in classified),
        "count_xfail_marked": sum(row["xfail_marked"] for row in classified),
        "executed": None, "passed": None, "failed": None,
        "skipped_at_runtime": None, "xfailed_at_runtime": None,
        "note": "Collection only: markers are not actual skip/xfail outcomes. "
                "Plugin package ownership is path-derived; root owners, "
                "business objects and levels require explicit evidence.",
        "matrix": [
            {"owner_architecture": o, "business_object": b,
             "test_level": l, "count": count}
            for (o, b, l), count in sorted(matrix.items())
        ],
        "items": classified,
    }


def write_verified_collection(
    report: dict[str, Any], output: Path, *, collection_exit_code: int,
) -> None:
    """Replace a prior successful inventory only with another complete one.

    Collection imports fixtures and may abort after yielding a subset of
    items. Never publish that subset as a current baseline, nor overwrite an
    earlier successful report. Write atomically to avoid truncated JSON.
    """
    if collection_exit_code != 0:
        raise ValueError("collection_incomplete")
    if not report["items"] or report["count_collected"] != len(report["items"]):
        raise ValueError("collection_empty_or_inconsistent")
    if not isinstance(report["source_sha"], str) or not _SHA_RE.fullmatch(report["source_sha"]):
        raise ValueError("missing_exact_git_head")
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    temp: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=output.parent,
            prefix=".cifereca-collection-", suffix=".tmp", delete=False,
        ) as stream:
            temp = Path(stream.name)
            stream.write(payload)
        os.replace(temp, output)
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", required=True, help="tests or src/plugins/<package>/tests")
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args(argv)
    if not _SUITE_RE.fullmatch(arguments.suite):
        parser.error("suite must be exactly tests or src/plugins/<package>/tests")
    suite_dir = ROOT / arguments.suite
    if not suite_dir.is_dir() or suite_dir.is_symlink():
        parser.error("suite must be an existing non-symlink test directory")
    mapping = validate_manifest(json.loads(arguments.manifest.read_text(encoding="utf-8")))
    import pytest  # no pytest import until explicitly invoked
    collector = CollectionCapture()
    result = pytest.main(["--collect-only", "-q", arguments.suite], plugins=[collector])
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT,
            capture_output=True, text=True, check=True, timeout=10,
        ).stdout.strip()
        source_sha = head if _SHA_RE.fullmatch(head) else None
    except (OSError, subprocess.SubprocessError):
        source_sha = None
    report = make_report(collector.items, mapping, suite=arguments.suite,
                         source_sha=source_sha)
    report["collection_exit_code"] = int(result)
    try:
        write_verified_collection(
            report, arguments.output, collection_exit_code=int(result),
        )
    except ValueError as exc:
        print(
            f"Collection not published: {exc} "
            f"(exit={int(result)}, collected={report['count_collected']}). "
            "An existing successful report was left unchanged.",
            file=sys.stderr,
        )
        return 1
    unclassified = sum(
        row["count"] for row in report["matrix"]
        if "unclassified" in (
            row["owner_architecture"], row["business_object"], row["test_level"],
        )
    )
    print(
        f"collected={report['count_collected']} "
        f"collection_exit_code={int(result)} unclassified={unclassified}"
    )
    return 0 if int(result) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
