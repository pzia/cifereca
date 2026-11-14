"""#5376: weekly source-only inventory must not silently become a pytest gate."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_weekly_inventory_is_manual_or_scheduled_on_exact_main():
    workflow = (
        ROOT / ".github/workflows/p0-test-dependency-inventory.yml"
    ).read_text(encoding="utf-8")
    assert "workflow_dispatch:" in workflow
    assert "schedule:" in workflow
    assert 'cron: "17 4 * * 0"' in workflow
    assert 'test "$GITHUB_REF" = "refs/heads/main"' in workflow
    assert 'test "$observed" = "$GITHUB_SHA"' in workflow
    assert 'test -z "$(git status --porcelain --untracked-files=no)"' in workflow
    assert "persist-credentials: false" in workflow
    assert "permissions:\n  contents: read" in workflow


def test_weekly_inventory_only_scans_sources_and_checks_completeness():
    workflow = (
        ROOT / ".github/workflows/p0-test-dependency-inventory.yml"
    ).read_text(encoding="utf-8")
    assert "python3 -m scripts.ci_test_core_consumer_graph --json" in workflow
    assert "python3 -m scripts.ci_test_provider_import_inventory --json" in workflow
    assert 'graph["parse_errors"]' in workflow
    assert 'providers["parse_or_io_errors"]' in workflow
    assert 'graph["root_test_files"]' in workflow
    assert 'providers["provider_modules_indexed"]' in workflow
    assert '"runtime_pytest_tests_executed": 0' in workflow
    assert "actions/upload-artifact@v4" in workflow
    assert "if-no-files-found: error" in workflow
    assert "name: p0-test-dependency-inventory-${{ github.sha }" in workflow
    assert "retention-days: 30" in workflow
    assert "pytest --collect-only" not in workflow
    assert "python -m pytest" not in workflow
    assert "python3 -m pytest" not in workflow
    assert "secrets." not in workflow
