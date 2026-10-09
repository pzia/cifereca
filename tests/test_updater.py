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


@pytest.mark.parametrize(("managed_current", "managed_target"), [(True, False), (False, True), (True, True)])
def test_rollback_cannot_claim_success_without_managed_plugin_restoration(
    tmp_path, monkeypatch, managed_current, managed_target,
):
    """No Core pointer may move when either generation declares managed plugins."""
    root = tmp_path / "runtime"
    releases = root / "releases"
    previous = releases / "1.0.0-a"
    current = releases / "2.0.0-b"
    for release in (previous, current):
        (release / ".venv" / "bin").mkdir(parents=True)
    manifest = '{"schema": 1, "packages": {"org.test": {"version": "0.1.0"}}}\n'
    for release, managed in ((current, managed_current), (previous, managed_target)):
        if managed:
            folder = release / "src" / "cifereca"
            folder.mkdir(parents=True)
            (folder / "plugin-release.json").write_text(manifest, encoding="utf-8")
    root.mkdir(parents=True, exist_ok=True)
    (root / "current").symlink_to(os.path.relpath(current, root))
    (root / "previous").symlink_to(os.path.relpath(previous, root))
    monkeypatch.setenv("CIFERECA_INSTALL_ROOT", str(root))
    monkeypatch.setenv("CIFERECA_BIN_DIR", str(tmp_path / "bin"))
    monkeypatch.setattr(updater, "_smoke", lambda _release: pytest.fail("unsafe rollback reached smoke"))
    with pytest.raises(updater.UpdateError, match="Rollback Core \\+ plugins indisponible"):
        updater.rollback()
    assert updater._symlink_target(root / "current").resolve() == current.resolve()
    assert updater._symlink_target(root / "previous").resolve() == previous.resolve()
    assert not (tmp_path / "bin").exists()


def test_rollback_cli_reports_error_without_moving_managed_core(tmp_path, monkeypatch, capsys):
    root = tmp_path / "runtime"
    a = root / "releases" / "1.0.0-a"
    b = root / "releases" / "2.0.0-b"
    for release in (a, b):
        (release / ".venv" / "bin").mkdir(parents=True)
    path = b / "src" / "cifereca" / "plugin-release.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        '{"schema": 1, "packages": {"org.test": {"version": "0.1.0"}}}\n',
        encoding="utf-8",
    )
    root.mkdir(parents=True, exist_ok=True)
    (root / "current").symlink_to(os.path.relpath(b, root))
    (root / "previous").symlink_to(os.path.relpath(a, root))
    monkeypatch.setenv("CIFERECA_INSTALL_ROOT", str(root))
    monkeypatch.setenv("CIFERECA_BIN_DIR", str(tmp_path / "bin"))
    assert updater.main(["--rollback", "--json"]) == 1
    result = updater.json.loads(capsys.readouterr().out)
    assert result["ok"] is False
    assert result["error"]["code"] == "update_error"
    assert "snapshot restaurable" in result["error"]["message"]
    assert updater._symlink_target(root / "current").resolve() == b.resolve()


def test_github_dry_run_resolves_sha_without_mutating_runtime(tmp_path, monkeypatch):
    target = tmp_path / "runtime"
    monkeypatch.setenv("CIFERECA_INSTALL_ROOT", str(target))
    monkeypatch.setattr(updater.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(updater, "select_python", lambda explicit=None: os.sys.executable)

    def fake_run(command, **kwargs):
        command = [str(x) for x in command]
        if command[1:3] == ["api", "repos/pzia/cifereca/commits/main"]:
            return SimpleNamespace(returncode=0, stdout="abc123\n", stderr="")
        if command[1:3] == ["repo", "clone"]:
            source = Path(command[4])
            _mini_project(source, "9.9.9")
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if "fetch" in command or "checkout" in command:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        raise AssertionError(command)

    monkeypatch.setattr(updater.subprocess, "run", fake_run)
    result = updater.install_github("main", dry_run=True, python=os.sys.executable)

    assert result["dry_run"] is True
    assert result["github_commit"] == "abc123"
    assert result["github_repository"] == "pzia/cifereca"
    assert result["provenance"]["ref"] == "main"
    assert not target.exists()


@pytest.mark.parametrize(
    ("ref", "api_ref"),
    [
        ("v0.9.44-dev.1", "v0.9.44-dev.1"),
        ("refs/tags/v0.9.44-dev.1", "v0.9.44-dev.1"),
    ],
)
def test_github_dry_run_accepts_tags(tmp_path, monkeypatch, ref, api_ref):
    target = tmp_path / "runtime"
    monkeypatch.setenv("CIFERECA_INSTALL_ROOT", str(target))
    monkeypatch.setattr(updater.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(updater, "select_python", lambda explicit=None: os.sys.executable)

    def fake_run(command, **kwargs):
        command = [str(x) for x in command]
        if command[1:3] == ["api", f"repos/pzia/cifereca/commits/{api_ref}"]:
            return SimpleNamespace(returncode=0, stdout="tagged123\n", stderr="")
        if command[1:3] == ["repo", "clone"]:
            source = Path(command[4])
            _mini_project(source, "9.9.9")
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if "fetch" in command or "checkout" in command:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        raise AssertionError(command)

    monkeypatch.setattr(updater.subprocess, "run", fake_run)
    result = updater.install_github(ref, dry_run=True, python=os.sys.executable)

    assert result["github_ref"] == ref
    assert result["github_commit"] == "tagged123"
    assert result["provenance"]["ref"] == ref
    assert not target.exists()


def test_backup_persistent_state_copies_db_config_and_plugins(tmp_path, monkeypatch):
    config_home = tmp_path / "config-home"
    data_home = tmp_path / "data-home"
    state_home = tmp_path / "state-home"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    monkeypatch.setenv("XDG_DATA_HOME", str(data_home))
    monkeypatch.setenv("XDG_STATE_HOME", str(state_home))

    cfg = updater.config_dir()
    (cfg / "config.json").write_text('{"ok": true}\n', encoding="utf-8")
    db = updater.database_file()
    with updater.sqlite3.connect(db) as conn:
        conn.execute("create table sample(value text)")
        conn.execute("insert into sample values ('kept')")
    plugins = updater.data_dir() / "plugins"
    plugins.mkdir(parents=True)
    (plugins / "state.json").write_text('{"active": true}\n', encoding="utf-8")

    backup = updater._backup_persistent_state("abc123")

    assert (backup / "config" / "config.json").is_file()
    assert (backup / "plugins" / "state.json").is_file()
    with updater.sqlite3.connect(backup / "library.sqlite3") as conn:
        assert conn.execute("select value from sample").fetchone()[0] == "kept"
    metadata = (backup / "backup.json").read_text(encoding="utf-8")
    assert "abc123" in metadata



def _release_with_plugin_manifest(root: Path, version: str = "9.9.9") -> Path:
    source = _mini_project(root, version)
    manifest_dir = source / "src" / "cifereca"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "plugin-release.json").write_text(
        '{"schema":1,"release":"' + version + '","packages":{"org.test":{"version":"2.0.0"}}}\n',
        encoding="utf-8",
    )
    plugins = source / "src" / "plugins" / "test"
    plugins.mkdir(parents=True)
    (plugins / "cifereca-plugin.json").write_text("{}\n", encoding="utf-8")
    scripts = source / "scripts"
    scripts.mkdir()
    (scripts / "package-dev-snapshot.py").write_text("# fixture\n", encoding="utf-8")
    return source


def test_standard_update_applies_release_plugins_after_core_switch(tmp_path, monkeypatch):
    source = _release_with_plugin_manifest(tmp_path / "source")
    root = tmp_path / "runtime"
    monkeypatch.setenv("CIFERECA_INSTALL_ROOT", str(root))
    monkeypatch.setenv("CIFERECA_BIN_DIR", str(tmp_path / "bin"))
    monkeypatch.setattr(updater, "select_python", lambda explicit=None: os.sys.executable)

    def fake_venv(release, python):
        bindir = release / ".venv" / "bin"
        bindir.mkdir(parents=True)
        for name in (*updater.PUBLIC_COMMANDS, updater.DEV_COMMAND):
            path = bindir / name
            path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            path.chmod(0o755)
        (bindir / "python").write_text("", encoding="utf-8")
        return release / ".venv"

    monkeypatch.setattr(updater, "_create_venv", fake_venv)
    monkeypatch.setattr(updater, "_smoke", lambda release: {})
    snapshot = tmp_path / "snapshot.zip"

    def fake_build(release, destination):
        snapshot.write_bytes(b"snapshot")
        return snapshot

    applied = []
    monkeypatch.setattr(updater, "_build_release_plugin_snapshot", fake_build)
    monkeypatch.setattr(
        updater,
        "_apply_release_plugin_snapshot",
        lambda release, snap: applied.append((release, snap)) or {"ok": True, "plugins": ["org.test"]},
    )

    result = updater.install_source(source)
    assert applied
    assert applied[0][0].resolve() == Path(result["current"]).resolve()
    assert applied[0][1] == snapshot
    assert result["plugins"]["plugins"] == ["org.test"]


def test_plugin_upgrade_failure_restores_previous_core(tmp_path, monkeypatch):
    old = tmp_path / "runtime" / "releases" / "1.0.0-old"
    (old / ".venv" / "bin").mkdir(parents=True)
    root = tmp_path / "runtime"
    root.mkdir(parents=True, exist_ok=True)
    (root / "current").symlink_to(os.path.relpath(old, root))
    source = _release_with_plugin_manifest(tmp_path / "source", "2.0.0")
    monkeypatch.setenv("CIFERECA_INSTALL_ROOT", str(root))
    monkeypatch.setenv("CIFERECA_BIN_DIR", str(tmp_path / "bin"))
    monkeypatch.setattr(updater, "select_python", lambda explicit=None: os.sys.executable)

    def fake_venv(release, python):
        bindir = release / ".venv" / "bin"
        bindir.mkdir(parents=True)
        for name in (*updater.PUBLIC_COMMANDS, updater.DEV_COMMAND):
            path = bindir / name
            path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            path.chmod(0o755)
        (bindir / "python").write_text("", encoding="utf-8")
        return release / ".venv"

    monkeypatch.setattr(updater, "_create_venv", fake_venv)
    monkeypatch.setattr(updater, "_smoke", lambda release: {})
    monkeypatch.setattr(updater, "_build_release_plugin_snapshot", lambda release, dest: tmp_path / "snapshot.zip")
    monkeypatch.setattr(
        updater,
        "_apply_release_plugin_snapshot",
        lambda *_: (_ for _ in ()).throw(updater.UpdateError("plugin failed")),
    )

    with pytest.raises(updater.UpdateError, match="plugin failed"):
        updater.install_source(source)
    assert updater._symlink_target(root / "current").resolve() == old.resolve()


def test_release_without_plugin_manifest_keeps_core_only_update_contract(tmp_path):
    source = _mini_project(tmp_path / "source")
    assert updater._release_plugin_manifest(source) == {}
