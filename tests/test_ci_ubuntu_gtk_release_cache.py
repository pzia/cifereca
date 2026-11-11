"""Ubuntu 24.04 GTK Release cache: strict platform isolation and publication boundary."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tarfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = ROOT / "scripts/ci-validate-gtk-relay-cache.py"
WORKFLOW = ROOT / ".github/workflows/gtk-ubuntu24-cache-asset.yml"
FETCHER = ROOT / "environnements/chatgpt-work/fetch_release_cache.py"


def _validator():
    spec = importlib.util.spec_from_file_location("gtk_cache_validator", VALIDATOR)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_noble_cache_marker_is_required_and_cannot_mix_debian(tmp_path):
    mod = _validator()
    cache = tmp_path / "cache"
    cache.mkdir()
    marker = cache / mod.MARKER
    output = tmp_path / "out.tar.gz"
    for i in range(12):
        (cache / f"gtkfixture{i}_1.0_amd64.deb").write_bytes(
            b"!<arch>\n" + b"synthetic data"
        )
    marker.write_text(json.dumps(mod.PROFILE), encoding="utf-8")
    with pytest.raises(ValueError):
        mod.build(cache, output, profile=mod.UBUNTU_PROFILE)
    assert not output.exists()
    marker.write_text(json.dumps(mod.UBUNTU_PROFILE), encoding="utf-8")
    assert mod.build(cache, output, profile=mod.UBUNTU_PROFILE) == 12
    with tarfile.open(output, "r:gz") as archive:
        assert json.load(archive.extractfile(".gtk-deb-cache/" + mod.MARKER)) == mod.UBUNTU_PROFILE
    output.unlink()
    with pytest.raises(ValueError):
        mod.build(cache, output, profile={"distro": "private", "codename": "noble", "architecture": "amd64"})
    assert not output.exists()


def test_release_is_explicit_immutable_no_checkout_publish_and_sha_verified():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    fetch = FETCHER.read_text(encoding="utf-8")
    assert "workflow_dispatch:" in workflow
    assert "github.ref == 'refs/heads/main'" in workflow
    assert "inputs.confirm_publish == true" in workflow
    assert "default: false" in workflow
    assert "container:" in workflow and "ubuntu:24.04" in workflow
    assert "ci-validate-gtk-relay-cache.py --platform ubuntu24" in workflow
    assert "gh release create" in workflow and "gh release view" in workflow
    assert "--prerelease" in workflow
    assert "Refusing to overwrite existing Ubuntu cache release" in workflow
    assert workflow.split("  publish:", 1)[1].count("actions/checkout") == 0
    assert "persist-credentials: false" in workflow
    assert "retention-days: 2" in workflow
    assert "ubuntu" in fetch and "noble" in fetch
    assert "required=True" in fetch
    assert "checksum mismatch" in fetch
    assert "destination already exists" in fetch
    assert "unsupported host" in fetch
    assert "unreviewed Ubuntu archive" in fetch
