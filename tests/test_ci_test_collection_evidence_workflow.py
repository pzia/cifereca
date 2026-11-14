"""Safety contracts for opt-in pytest collection evidence workflow (#5376).

No GitHub Actions run or provider import is needed for these checks.
"""
from __future__ import annotations

from pathlib import Path

WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/test-collection-evidence.yml"


def test_collection_evidence_is_explicitly_manual_and_read_only():
    source = WORKFLOW.read_text(encoding="utf-8")
    assert "  workflow_dispatch:" in source
    assert "\n  pull_request:" not in source
    assert "\n  push:" not in source
    assert "\n  schedule:" not in source
    assert "  contents: read" in source
    assert "persist-credentials: false" in source
    assert "timeout-minutes: 20" in source


def test_collection_evidence_has_bounded_named_suites():
    source = WORKFLOW.read_text(encoding="utf-8")
    for name, path in (
        ("core", "tests"),
        ("provider-paris", "src/plugins/provider-paris/tests"),
        ("provider-kobo", "src/plugins/provider-kobo/tests"),
        ("provider-numilog", "src/plugins/provider-numilog/tests"),
    ):
        assert f"            {name}) suite=\"{path}\" ;;" in source
        assert f"          - {name}" in source
    assert '*) echo "Invalid collection scope" >&2; exit 2 ;;' in source
    assert 'test -d "$suite"' in source
    assert '--suite "$SUITE_PATH"' in source


def test_collection_evidence_fails_closed_before_artifact_upload():
    source = WORKFLOW.read_text(encoding="utf-8")
    assert "set -euo pipefail" in source
    assert 'report["source_sha"] == sys.argv[2]' in source
    assert 'report["suite"] == sys.argv[3]' in source
    assert 'report["collection_exit_code"] == 0' in source
    assert 'report["count_collected"] > 0' in source
    assert 'sum(row["count"] for row in report["matrix"]) == report["count_collected"]' in source
    assert "if: success()" in source
    assert "actions/upload-artifact@" not in source
    assert '>> "$GITHUB_STEP_SUMMARY"' in source
    assert "name: Publish report summary" in source
    assert "executed/passed/failed: not measured" in source.lower()
