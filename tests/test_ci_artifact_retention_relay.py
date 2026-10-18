"""Do not publish untracked GTK relay files or retain ephemeral ZIPs for months (#1143)."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / ".github/workflows"


def _workflow(filename: str) -> str:
    return (ROOT / filename).read_text(encoding="utf-8")


def test_debian_gtk_relay_archives_only_version_controlled_files():
    source = _workflow("gtk-e2e-on-approval.yml")
    assert "git archive --format=tar.gz --output=/tmp/cifereca-gtk-source.tar.gz HEAD" in source
    assert "tar --exclude=.git" not in source
    assert "persist-credentials: false" in source
    assert "retention-days: 2" in source
    assert "workflow_dispatch:" not in source


def test_manual_package_zip_has_short_explicit_retention():
    source = _workflow("package-plugins.yml")
    assert "workflow_dispatch:" in source
    assert "name: cifereca-plugin-packages" in source
    assert "retention-days: 2" in source


def test_plugins_preview_zip_has_short_explicit_retention():
    source = _workflow("plugins-only-preview.yml")
    assert "workflow_dispatch:" in source
    assert "cifereca-plugins-only-preview" in source
    assert "retention-days: 2" in source
    assert "retention-days: 14" not in source
