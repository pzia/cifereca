"""CI #5376: GTK bootstrap fails closed with finite APT operations."""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/ci-install-gtk-e2e-runtime.sh"


def test_gtk_apt_operations_are_bounded_and_keep_retry_finite():
    script = SCRIPT.read_text(encoding="utf-8")
    assert script.count("timeout --signal=TERM --kill-after=10s") == 3
    assert "240s sudo env DEBIAN_FRONTEND=noninteractive apt-get" in script
    assert "120s sudo env DEBIAN_FRONTEND=noninteractive apt-get" in script
    assert "Acquire::http::Timeout=25" in script
    assert "Acquire::https::Timeout=25" in script
    assert "DPkg::Lock::Timeout=30" in script
    assert script.count("DEBIAN_FRONTEND=noninteractive") == 3
    assert "Dpkg::Use-Pty=0" in script
    assert "GTK E2E Ubuntu archive install" in script
    assert "fallback_sources=\"$(mktemp)\"" in script
    assert "Dir::Etc::sourceparts=-" in script
    assert "ubuntu:noble" in script
    assert "deb https://archive.ubuntu.com/ubuntu noble" in script
    assert "deb https://security.ubuntu.com/ubuntu noble-security" in script
    assert "trusted=yes" not in script
    assert "AllowUnauthenticated" not in script
    assert "|| true" not in script
    assert "set -euo pipefail" in script
    assert "gi.require_version(\"Gtk\", \"4.0\")" in script


def test_gtk_bootstrap_shell_syntax_is_valid():
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)
