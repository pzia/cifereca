from __future__ import annotations

from pathlib import Path
import json
import os
import shlex
import subprocess
import sys

import pytest

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
    assert "https://archive.ubuntu.com/ubuntu" in source
    assert "http://archive.ubuntu.com/ubuntu" not in source
    assert "https://security.ubuntu.com/ubuntu" in source
    assert "http://security.ubuntu.com/ubuntu" not in source
    assert "VERSION_CODENAME:-" in source


def test_pr_ci_does_not_bootstrap_or_run_gtk_e2e():
    pr_ci = (ROOT / ".github" / "workflows" / "pr-ci.yml").read_text(encoding="utf-8")
    assert "bash scripts/ci-install-gtk-e2e-runtime.sh" not in pr_ci
    assert "e2e/run.py" not in pr_ci
    assert "steps.scope.outputs.gui" not in pr_ci


def test_manual_e2e_workflow_keeps_shared_runtime_bootstrap():
    e2e_smoke = (ROOT / ".github" / "workflows" / "e2e-smoke.yml").read_text(encoding="utf-8")
    assert "bash scripts/ci-install-gtk-e2e-runtime.sh" in e2e_smoke



@pytest.mark.parametrize("mode,distro,expected", [
    ("success", "ubuntu", 0), ("update_failure", "ubuntu", 100),
    ("success", "debian", 1),
])
def test_signed_fallback_executes_bounded_commands_and_propagates_failure(tmp_path, mode, distro, expected):
    """Shell orchestration only: fake OS/APT/runtime, no package installation."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.jsonl"

    def executable(name, body):
        path = bin_dir / name
        path.write_text(body)
        path.chmod(0o755)
        return path

    executable("dpkg-query", "#!/bin/sh\nexit 1\n")
    for name in ("xvfb-run", "dbus-run-session"):
        executable(name, "#!/bin/sh\nexit 0\n")
    executable("sudo", f"#!{sys.executable}\n" + """
import json,os,sys
args=sys.argv[1:]
with open(os.environ['APT_CALL_LOG'],'a') as stream: stream.write(json.dumps(args)+'\\n')
alternate=any(arg.startswith('Dir::Etc::sourcelist=') for arg in args)
if not alternate: sys.exit(100)
if 'update' in args and os.environ['APT_FAKE_MODE']=='update_failure': sys.exit(100)
""")
    runtime = executable("runtime-validation", f"#!{sys.executable}\n" + """
import json,os
with open(os.environ['APT_CALL_LOG'],'a') as stream: stream.write(json.dumps(['runtime-validation'])+'\\n')
""")
    os_release = tmp_path / "os-release"
    os_release.write_text(f"ID={distro}\nVERSION_CODENAME=noble\n")
    source = BOOTSTRAP.read_text()
    assert source.count(". /etc/os-release") == 1
    assert source.count("/usr/bin/python3 - <<'PY'") == 1
    # Replace environment boundaries only; execute the actual shell logic.
    source = source.replace(". /etc/os-release", ". " + shlex.quote(str(os_release)))
    source = source.replace("/usr/bin/python3 - <<'PY'", shlex.quote(str(runtime)) + " - <<'PY'")
    target = tmp_path / "bootstrap.sh"
    target.write_text(source)
    result = subprocess.run(
        ["bash", str(target)], capture_output=True, text=True, timeout=5,
        env={**os.environ, "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
             "RUNNER_TEMP": str(tmp_path), "APT_CALL_LOG": str(log), "APT_FAKE_MODE": mode},
    )
    assert result.returncode == expected, result.stderr
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert "75s" in calls[0] and "Acquire::Retries=0" in calls[0]
    if distro != "ubuntu":
        assert len(calls) == 1 and "unrecognized distro" in result.stderr
        return
    fallback_update = calls[1]
    assert "90s" in fallback_update and "update" in fallback_update
    for option in ("Acquire::Retries=1", "Acquire::Languages=none",
                   "Acquire::IndexTargets::deb::CNF::DefaultEnabled=false"):
        assert option in fallback_update
    sources = (tmp_path / "cifereca-gtk-ubuntu.sources.list").read_text()
    assert sources.count("signed-by=/usr/share/keyrings/ubuntu-archive-keyring.gpg") == 3
    assert sources.count("https://") == 3 and "trusted=yes" not in sources
    if mode == "update_failure":
        assert len(calls) == 2  # No install or validation after failed update.
    else:
        assert "150s" in calls[2] and "install" in calls[2]
        assert calls[3] == ["runtime-validation"] and len(calls) == 4
