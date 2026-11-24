from hashlib import sha256

import pytest

from cifereca.plugins import PackageStore, PluginManager, PluginStateStore
from cifereca.core.notice_consolidator_active_inventory import hold_active_consolidator_inventory
from tests.test_core_p1_4552_consolidator_archive import payload
from tests.test_core_p1_4552_consolidator_manifest import MEMBER, SOURCE


def test_active_inventory_uses_exact_installed_bytes_and_enabled_owner(tmp_path):
    data = payload()
    archive = tmp_path / "package.zip"
    archive.write_bytes(data)
    store = PackageStore(tmp_path / "packages")
    manager = PluginManager(package_store=store,
                            state_store=PluginStateStore(tmp_path / "state.json"))
    digest = sha256(data).hexdigest()
    try:
        package = manager.installer.install(archive)
        manager.refresh()
        with hold_active_consolidator_inventory(manager, data, expected_sha256=digest) as candidates:
            candidate, = candidates
            assert candidate.installed.source.encode() == SOURCE
        manager.state.set_enabled("plugin.age.alpha", False)
        with hold_active_consolidator_inventory(manager, data, expected_sha256=digest) as candidates:
            assert candidates == ()
        manager.state.set_enabled("plugin.age.alpha", True)
        manager.refresh()
        script = package.root / MEMBER
        script.write_bytes(SOURCE + b"# modified")
        with pytest.raises(ValueError, match="active_package_changed"):
            with hold_active_consolidator_inventory(manager, data, expected_sha256=digest):
                pytest.fail("modified installed script admitted")
        script.unlink()
        outside = tmp_path / "foreign.py"
        outside.write_bytes(SOURCE)
        script.symlink_to(outside)
        with pytest.raises(ValueError, match="active_package_changed"):
            with hold_active_consolidator_inventory(manager, data, expected_sha256=digest):
                pytest.fail("symlink admitted")
    finally:
        manager.close()
