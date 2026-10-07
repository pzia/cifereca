from __future__ import annotations

import os
from types import SimpleNamespace
import zipfile
from pathlib import Path

import pytest

from cifereca import updater


def _mini_project(root: Path, version: str = "9.9.9") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "VERSION").write_text(version + "\n", encoding="utf-8")
    (root / "pyproject.toml").write_text(
        f'''[project]\nname = "cifereca"\nversion = "{version}"\nrequires-python = ">=3.11"\n\n[project.scripts]\ncifereca = "x:main"\ncifereca-cli = "x:main"\ncifereca-update = "x:main"\n''',
        encoding="utf-8",
    )
    (root / "x.py").write_text("def main(): return 0\n", encoding="utf-8")
    return root


def test_update_dry_run_does_not_create_install_root(tmp_path, monkeypatch):
    source = _mini_project(tmp_path / "src")
    target = tmp_path / "runtime"
    monkeypatch.setenv("CIFERECA_INSTALL_ROOT", str(target))
    result = updater.install_source(source, python=os.sys.executable, dry_run=True)
    assert result["version"] == "9.9.9"
    assert result["dry_run"] is True
    assert not target.exists()


def test_archive_rejects_path_traversal(tmp_path):
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("Project/pyproject.toml", "[project]\nname='cifereca'\nversion='1.0.0'\n")
        z.writestr("Project/../../escape", "x")
    with pytest.raises(updater.UpdateError):
        with pytest.MonkeyPatch.context():
            updater.install_archive(archive, dry_run=True)


def test_archive_rejects_windows_drive_paths():
    info = zipfile.ZipInfo("C:/escape.txt")
    with pytest.raises(updater.UpdateError, match="Chemin interdit"):
        updater._safe_member(info)


def test_archive_resource_limits_are_enforced(tmp_path, monkeypatch):
    archive = tmp_path / "oversized.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("Project/pyproject.toml", "[project]\nname='cifereca'\nversion='1.0.0'\n")
        z.writestr("Project/a", "a")
        z.writestr("Project/b", "b")

    monkeypatch.setattr(updater, "MAX_RELEASE_FILES", 2)
    with pytest.raises(updater.UpdateError, match="nombre de fichiers"):
        updater.install_archive(archive, dry_run=True)

    monkeypatch.setattr(updater, "MAX_RELEASE_FILES", 20000)
    monkeypatch.setattr(updater, "MAX_RELEASE_UNCOMPRESSED_BYTES", 16)
    with pytest.raises(updater.UpdateError, match="après extraction"):
        updater.install_archive(archive, dry_run=True)


def test_create_venv_prefers_offline_core_wheelhouse(tmp_path, monkeypatch):
    release = _mini_project(tmp_path / "release")
    wheelhouse = release / "offline-wheels"
    wheelhouse.mkdir()
    (wheelhouse / "cifereca-9.9.9-py3-none-any.whl").write_bytes(b"wheel")

    commands: list[list[str]] = []

    def fake_run(command, **kwargs):
        command = [str(value) for value in command]
        commands.append(command)
        if command[1:3] == ["-m", "venv"]:
            py = release / ".venv" / "bin" / "python"
            py.parent.mkdir(parents=True)
            py.write_text("", encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(updater.subprocess, "run", fake_run)

    venv = updater._create_venv(release, os.sys.executable)

    assert venv == release / ".venv"
    assert len(commands) == 2
    pip = commands[1]
    assert pip[:4] == [str(release / ".venv" / "bin" / "python"), "-m", "pip", "install"]
    assert "--no-index" in pip
    assert "--find-links" in pip
    assert pip[pip.index("--find-links") + 1] == str(wheelhouse)
    assert "--ignore-installed" in pip
    assert "cifereca==9.9.9" in pip
    assert str(release) not in pip


def test_atomic_current_switch_and_previous(tmp_path, monkeypatch):
    source1 = _mini_project(tmp_path / "src1", "1.0.0")
    source2 = _mini_project(tmp_path / "src2", "2.0.0")
    root = tmp_path / "runtime"
    bindir = tmp_path / "bin"
    monkeypatch.setenv("CIFERECA_INSTALL_ROOT", str(root))
    monkeypatch.setenv("CIFERECA_BIN_DIR", str(bindir))

    def fake_create_venv(release: Path, python: str):
        b = release / ".venv" / "bin"
        b.mkdir(parents=True)
        for name in updater.PUBLIC_COMMANDS:
            p = b / name
            p.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            p.chmod(0o755)
        (b / "python").write_text("", encoding="utf-8")
        return release / ".venv"

    monkeypatch.setattr(updater, "_create_venv", fake_create_venv)
    monkeypatch.setattr(updater, "_smoke", lambda release: None)
    monkeypatch.setattr(updater, "select_python", lambda explicit=None: os.sys.executable)

    first = updater.install_source(source1)
    first_current = Path(first["current"])
    second = updater.install_source(source2)
    assert Path(second["current"]).name.startswith("2.0.0-")
    assert Path(second["previous"]).resolve() == first_current.resolve()
    launcher = bindir / "cifereca"
    assert launcher.is_file()
    assert "/current/.venv/bin/cifereca" in launcher.read_text(encoding="utf-8")


def test_rollback_repoints_current(tmp_path, monkeypatch):
    root = tmp_path / "runtime"
    bindir = tmp_path / "bin"
    releases = root / "releases"
    a = releases / "1.0.0-a"
    b = releases / "2.0.0-b"
    for release in (a, b):
        (release / ".venv" / "bin").mkdir(parents=True)
    root.mkdir(parents=True, exist_ok=True)
    (root / "current").symlink_to(os.path.relpath(b, root))
    (root / "previous").symlink_to(os.path.relpath(a, root))
    monkeypatch.setenv("CIFERECA_INSTALL_ROOT", str(root))
    monkeypatch.setenv("CIFERECA_BIN_DIR", str(bindir))
    monkeypatch.setattr(updater, "_smoke", lambda release: None)
    result = updater.rollback()
    assert Path(result["current"]).resolve() == a.resolve()
