from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP = ROOT / "scripts" / "ci-install-gtk-e2e-runtime.sh"


def test_gtk_e2e_bootstrap_reuses_preinstalled_runtime_before_apt_refresh():
    source = BOOTSTRAP.read_text(encoding="utf-8")
    assert "dpkg-query" in source
    assert "command -v xvfb-run" in source
    assert "command -v dbus-run-session" in source
    assert source.index("sudo apt-get install") < source.index("sudo apt-get update")


def test_pr_ci_does_not_bootstrap_or_run_gtk_e2e():
    pr_ci = (ROOT / ".github" / "workflows" / "pr-ci.yml").read_text(encoding="utf-8")
    assert "bash scripts/ci-install-gtk-e2e-runtime.sh" not in pr_ci
    assert "e2e/run.py" not in pr_ci
    assert "steps.scope.outputs.gui" not in pr_ci


def test_manual_e2e_workflow_keeps_shared_runtime_bootstrap():
    e2e_smoke = (ROOT / ".github" / "workflows" / "e2e-smoke.yml").read_text(encoding="utf-8")
    assert "bash scripts/ci-install-gtk-e2e-runtime.sh" in e2e_smoke
