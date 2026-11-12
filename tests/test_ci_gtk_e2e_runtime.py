from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP = ROOT / "scripts" / "ci-install-gtk-e2e-runtime.sh"


def test_gtk_e2e_bootstrap_reuses_preinstalled_runtime_before_apt_refresh():
    source = BOOTSTRAP.read_text(encoding="utf-8")
    assert "dpkg-query" in source
    assert "command -v xvfb-run" in source
    assert "command -v dbus-run-session" in source
    assert source.index('apt-get "${apt_options[@]}" install') < source.index('apt-get "${alternate_options[@]}" update')
    assert "sudo timeout --signal=TERM --kill-after=10s 75s" in source
    assert "sudo timeout --signal=TERM --kill-after=10s 90s" in source
    assert "sudo timeout --signal=TERM --kill-after=10s 150s" in source
    assert "signed-by=/usr/share/keyrings/ubuntu-archive-keyring.gpg" in source
    assert "http://archive.ubuntu.com/ubuntu" in source
    assert "http://security.ubuntu.com/ubuntu" in source
    assert "VERSION_CODENAME:-" in source


def test_pr_ci_does_not_bootstrap_or_run_gtk_e2e():
    pr_ci = (ROOT / ".github" / "workflows" / "pr-ci.yml").read_text(encoding="utf-8")
    assert "bash scripts/ci-install-gtk-e2e-runtime.sh" not in pr_ci
    assert "e2e/run.py" not in pr_ci
    assert "steps.scope.outputs.gui" not in pr_ci


def test_manual_e2e_workflow_keeps_shared_runtime_bootstrap():
    e2e_smoke = (ROOT / ".github" / "workflows" / "e2e-smoke.yml").read_text(encoding="utf-8")
    assert "bash scripts/ci-install-gtk-e2e-runtime.sh" in e2e_smoke
