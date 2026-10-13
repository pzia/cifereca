"""Static no-network publication gates for CI/CD artifact privacy (#1143)."""
from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"


def _workflow(name: str) -> str:
    return (WORKFLOWS / name).read_text(encoding="utf-8")


def test_e2e_workflow_never_uploads_raw_debug_artifacts_or_echoes_raw_summary():
    workflow = _workflow("e2e-smoke.yml")
    assert "python3 scripts/ci_publish_e2e_safe_summary.py" in workflow
    assert "path: e2e-public/summary.json" in workflow
    assert "cat e2e-public/summary.json" in workflow
    assert "path: e2e-artifacts/" not in workflow
    assert "cat e2e-artifacts/summary.json" not in workflow
    assert "name: e2e-core-debug" not in workflow
    assert "retention-days: 2" in workflow


def test_manual_gtk_artifacts_have_explicit_two_day_retention():
    workflow = _workflow("gtk-smoke.yml")
    assert "workflow_dispatch:" in workflow
    assert "retention-days: 2" in workflow


def test_plugin_archives_have_short_explicit_retention():
    for name in ("package-plugins.yml", "plugins-only-preview.yml"):
        workflow = _workflow(name)
        assert "workflow_dispatch:" in workflow
        assert "retention-days: 2" in workflow
        assert "retention-days: 14" not in workflow
