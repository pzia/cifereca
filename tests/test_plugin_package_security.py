from __future__ import annotations

import json
from pathlib import Path
import stat
import zipfile

import pytest

from cifereca.plugins import (
    PackageInstallError,
    PackageStore,
    PluginManager,
    PluginStateStore,
)
from cifereca.plugins import packages as package_module


def _manager(tmp_path: Path) -> PluginManager:
    return PluginManager(
        package_store=PackageStore(tmp_path / "packages"),
        state_store=PluginStateStore(tmp_path / "plugins-state.json"),
    )


def _manifest(package_id: str, plugin_id: str, *, entrypoint: str = "plugin:PLUGIN") -> dict:
    return {
        "manifest": 1,
        "package": {
            "id": package_id,
            "version": "1.0.0",
            "name": package_id,
            "publisher": "tests",
            "plugin_api": ">=1,<2",
        },
        "plugins": [{
            "id": plugin_id,
            "type": "transport",
            "entrypoint": entrypoint,
        }],
    }


def test_plugin_zip_rejects_cross_platform_unsafe_paths_and_symlinks(tmp_path: Path):
    manager = _manager(tmp_path / "state")
    manifest = _manifest("org.test.unsafe", "test.unsafe")
    try:
        for index, member in enumerate(("/absolute.py", "C:/windows-drive.py", r"C:\windows-drive.py")):
            archive = tmp_path / f"unsafe-{index}.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("cifereca-plugin.json", json.dumps(manifest))
                bundle.writestr(member, "evil=True")
            with pytest.raises(PackageInstallError, match="unsafe plugin archive path"):
                manager.inspect_archive(archive)

        archive = tmp_path / "symlink.zip"
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.writestr("cifereca-plugin.json", json.dumps(manifest))
            info = zipfile.ZipInfo("plugin.py")
            info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            bundle.writestr(info, "/tmp/evil")
        with pytest.raises(PackageInstallError, match="symlink"):
            manager.inspect_archive(archive)
    finally:
        manager.close()


def test_plugin_zip_resource_limits_are_enforced(tmp_path: Path, monkeypatch):
    manager = _manager(tmp_path / "state")
    manifest = _manifest("org.test.limit", "test.limit")
    archive = tmp_path / "many.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("cifereca-plugin.json", json.dumps(manifest))
        bundle.writestr("plugin.py", "PLUGIN=None")
        bundle.writestr("extra-a", "a")
        bundle.writestr("extra-b", "b")

    try:
        monkeypatch.setattr(package_module, "MAX_FILES", 3)
        with pytest.raises(PackageInstallError, match="too many files"):
            manager.inspect_archive(archive)

        monkeypatch.setattr(package_module, "MAX_FILES", 5000)
        monkeypatch.setattr(package_module, "MAX_UNCOMPRESSED_BYTES", 32)
        with pytest.raises(PackageInstallError, match="too large"):
            manager.inspect_archive(archive)
    finally:
        manager.close()


def test_plugin_entrypoint_must_resolve_inside_installed_package(tmp_path: Path):
    archive = tmp_path / "outside.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr(
            "cifereca-plugin.json",
            json.dumps(_manifest(
                "org.test.outside",
                "test.outside",
                entrypoint="json:loads",
            )),
        )

    manager = _manager(tmp_path / "state")
    try:
        manager.install(archive)
        status = manager.status("test.outside")
        assert status.state == "error"
        assert "outside its package" in status.error or "no package file" in status.error
    finally:
        manager.close()
