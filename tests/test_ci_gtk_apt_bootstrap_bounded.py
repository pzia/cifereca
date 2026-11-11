"""CI #5376: GTK bootstrap fails closed with finite APT operations."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/ci-install-gtk-e2e-runtime.sh"


def test_gtk_apt_operations_are_bounded_and_keep_retry_finite():
    script = SCRIPT.read_text(encoding="utf-8")
    assert script.count("timeout --signal=TERM --kill-after=10s") == 3
    assert "240s sudo apt-get" in script
    assert "120s sudo apt-get" in script
    assert "Acquire::http::Timeout=25" in script
    assert "Acquire::https::Timeout=25" in script
    assert "DPkg::Lock::Timeout=30" in script
    assert "GTK E2E APT retry" in script
    assert "|| true" not in script
    assert "set -euo pipefail" in script
    assert "gi.require_version(\"Gtk\", \"4.0\")" in script
