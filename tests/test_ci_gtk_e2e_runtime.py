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


def test_pr_and_manual_e2e_workflows_share_the_same_runtime_bootstrap():
    pr_ci = (ROOT / ".github" / "workflows" / "pr-ci.yml").read_text(encoding="utf-8")
    e2e_smoke = (ROOT / ".github" / "workflows" / "e2e-smoke.yml").read_text(encoding="utf-8")
    invocation = "bash scripts/ci-install-gtk-e2e-runtime.sh"
    assert invocation in pr_ci
    assert invocation in e2e_smoke
    assert '"scripts/ci-install-gtk-e2e-runtime.sh"' in pr_ci


def test_gtk_bootstrap_change_forces_real_e2e_validation():
    pr_ci = (ROOT / ".github" / "workflows" / "pr-ci.yml").read_text(encoding="utf-8")
    assert "scripts/ci-install-gtk-e2e-runtime\\.sh$" in pr_ci
    assert "e2e_scenario=all" in pr_ci
