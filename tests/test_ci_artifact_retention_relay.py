"""Do not publish untracked GTK relay files or retain ephemeral ZIPs for months (#1143)."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / ".github/workflows"


def _workflow(filename: str) -> str:
    return (ROOT / filename).read_text(encoding="utf-8")


def test_debian_gtk_relay_never_archives_checkout_or_unreviewed_cache():
    source = _workflow("gtk-e2e-on-approval.yml")
    assert "scripts/ci-validate-gtk-relay-cache.py" in source
    assert "git archive --format=tar.gz" not in source
    assert "tar -czf" not in source
    assert "/tmp/cifereca-gtk-source.tar.gz" not in source
    assert ".github/gtk-e2e-run-request.txt" in source
    assert "types: [synchronize]" in source
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
