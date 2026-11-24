from hashlib import sha256

import pytest

from cifereca.plugins import PackageStore, PluginManager, PluginStateStore
from cifereca.storage import Storage
from cifereca.core.notice_descriptive_active_config import reconcile_active_descriptive_selection
from tests.test_core_p1_4552_consolidator_archive import payload
from tests.test_core_p1_4552_consolidator_manifest import MEMBER, SOURCE


def test_active_selection_reconciliation(tmp_path):
    data = payload()
    archive = tmp_path / "package.zip"
    archive.write_bytes(data)
    manager = PluginManager(package_store=PackageStore(tmp_path / "packages"),
                            state_store=PluginStateStore(tmp_path / "state.json"))
    storage = Storage(tmp_path / "selection.sqlite")
    args = dict(expected_sha256=sha256(data).hexdigest(), canonical_type="age",
                scope="fr:editions", selected_plugin_id="plugin.age.alpha",
                selected_version="1.0.0")
    def reconcile(generation):
        return reconcile_active_descriptive_selection(
            storage, manager, data, expected_generation=generation, **args)
    try:
        package = manager.installer.install(archive)
        manager.refresh()
        first = reconcile(0)
        assert first.generation == 1 and first.selection.plugin_id == "plugin.age.alpha"
        assert first.schema_sha256 is not None
        assert reconcile(1) == first
        script = package.root / MEMBER
        script.write_bytes(SOURCE + b"# drift")
        with pytest.raises(ValueError, match="active_package_changed"):
            reconcile(1)
        script.write_bytes(SOURCE)
        assert reconcile(1) == first
        manager.state.set_enabled("plugin.age.alpha", False)
        second = reconcile(1)
        assert second.generation == 2 and second.selection is None
        assert reconcile(2) == second
    finally:
        manager.close()
        storage.close(final=True)
