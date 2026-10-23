"""Deterministic pre-publication offline archive checks for #2770."""
from __future__ import annotations

from datetime import datetime, timezone
import importlib.util
from pathlib import Path
import zipfile

import pytest

from cifereca.wheelhouse_quarantine import attest_wheelhouse


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "package-offline-release.py"
spec = importlib.util.spec_from_file_location("cifereca_offline_packager", MODULE_PATH)
assert spec is not None and spec.loader is not None
packager = importlib.util.module_from_spec(spec)
spec.loader.exec_module(packager)


def _release(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    pyproject = root / "pyproject.toml"
    pyproject.write_text('[project]\nname = "cifereca"\nversion = "9.9.9"\n')
    version = root / "VERSION"
    version.write_text("9.9.9\n")
    readme = root / "README.md"
    readme.write_text("Fake local source, no secrets\n")
    wheels = root / "offline-wheels"
    wheels.mkdir()
    wheel = wheels / "cifereca-9.9.9-py3-none-any.whl"
    wheel.write_bytes(b"local-Core-wheel")
    attest_wheelhouse(wheels, core_version="9.9.9", now=datetime.now(timezone.utc))
    return root, (pyproject, version, readme)


def test_cold_archive_is_one_root_with_provenance_and_exact_source(tmp_path):
    source, files = _release(tmp_path)
    output = tmp_path / "dist" / "offline.zip"
    result = packager.create_archive(source, output, files=files)
    assert result == output
    with zipfile.ZipFile(result) as bundle:
        assert bundle.testzip() is None
        names = set(bundle.namelist())
        assert names == {
            "cifereca-offline/pyproject.toml",
            "cifereca-offline/VERSION",
            "cifereca-offline/README.md",
            "cifereca-offline/offline-wheels/cifereca-9.9.9-py3-none-any.whl",
            "cifereca-offline/offline-wheels/wheelhouse-provenance.json",
        }
        assert bundle.read("cifereca-offline/VERSION") == b"9.9.9\n"


@pytest.mark.parametrize("case", ["no_proof", "tamper", "duplicate", "untracked", "symlink"])
def test_offline_archive_fails_closed_without_output(tmp_path, case):
    source, files = _release(tmp_path)
    wheels = source / "offline-wheels"
    if case == "no_proof":
        (wheels / "wheelhouse-provenance.json").unlink()
    elif case == "tamper":
        (wheels / "cifereca-9.9.9-py3-none-any.whl").write_bytes(b"modified")
    elif case == "duplicate":
        files = (*files, files[0])
    elif case == "untracked":
        hostile = tmp_path / "secret.txt"
        hostile.write_text("PRIVATE")
        files = (*files, hostile)
    else:
        target = tmp_path / "outside.txt"
        target.write_text("PRIVATE")
        symlink = source / "user-secret.txt"
        symlink.symlink_to(target)
        files = (*files, symlink)
    output = tmp_path / "offline.zip"
    with pytest.raises(ValueError):
        packager.create_archive(source, output, files=files)
    assert not output.exists()
