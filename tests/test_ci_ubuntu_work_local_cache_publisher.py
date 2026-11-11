"""The Work Ubuntu cache may be published without contacting Ubuntu mirrors."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tarfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "environnements/chatgpt-work/publish_cached_release.py"


def module():
    spec = importlib.util.spec_from_file_location("ubuntu_work_publisher", SCRIPT)
    assert spec and spec.loader
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def test_existing_cache_packages_are_strictly_reviewed_and_hashed(tmp_path):
    publisher = module()
    src = tmp_path / "packages"
    src.mkdir()
    for n in range(12):
        (src / f"libwork-gtk{n}_1.0_amd64.deb").write_bytes(
            b"!<arch>\n" + b"synthetic fixture only"
        )
    stage = tmp_path / "stage"
    stage.mkdir()
    archive, sha = publisher.build(stage, src)
    assert len(sha) == 64
    assert (stage / "SHA256SUMS").read_text() == f"{sha}  {publisher.ASSET}\n"
    with tarfile.open(archive, "r:gz") as tar:
        names = [m.name for m in tar.getmembers()]
        assert len(names) == 13
        profile = json.load(tar.extractfile(".gtk-deb-cache/.gtk4-cache-profile.json"))
        assert profile == {"distro": "ubuntu", "codename": "noble", "architecture": "amd64"}
        assert all(n.startswith(".gtk-deb-cache/") for n in names)


@pytest.mark.parametrize("invalid", ["private.env", "unsafe-symlink", "duplicate-escaped"])
def test_dangerous_cached_file_is_refused(tmp_path, invalid):
    publisher = module()
    src = tmp_path / "packages"
    src.mkdir()
    for n in range(12):
        (src / f"gtkfixture{n}_1.0_amd64.deb").write_bytes(b"!<arch>\nvalid")
    if invalid == "private.env":
        (src / "x_1.0_amd64.deb").write_bytes(b"PRIVATE")
    elif invalid == "unsafe-symlink":
        (src / "x_1.0_amd64.deb").symlink_to(src / "gtkfixture0_1.0_amd64.deb")
    else:
        (src / "foo_1%3a2_amd64.deb").write_bytes(b"!<arch>\nvalid")
        (src / "foo_1:2_amd64.deb").write_bytes(b"!<arch>\nvalid")
    stage = tmp_path / "stage"
    stage.mkdir()
    with pytest.raises(ValueError):
        publisher.build(stage, src)


def test_publish_requires_explicit_opt_in_and_never_invokes_apt():
    source = SCRIPT.read_text(encoding="utf-8")
    assert 'parser.add_argument("--publish", action="store_true"' in source
    assert 'run(str(BASE / "work"), "bootstrap", "--offline"' in source
    assert 'run(str(BASE / "work"), "check-import"' in source
    assert 'gh", "release", "create"' in source
    assert '--prerelease' in source
    assert '--clobber' not in source
    assert 'apt-get' not in source
    assert "ubuntu" in source and "noble" in source
    assert 'sys.version_info[:2] != (3, 12)' in source
