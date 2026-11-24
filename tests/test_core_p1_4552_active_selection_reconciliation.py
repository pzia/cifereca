from hashlib import sha256
import json

import pytest

from cifereca.plugins import PackageStore, PluginManager, PluginStateStore
from cifereca.storage import Storage
from cifereca.core.notice_descriptive_active_config import reconcile_active_descriptive_selection
from tests.test_core_p1_4552_consolidator_archive import payload
from tests.test_core_p1_4552_consolidator_manifest import MEMBER, SOURCE, row


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
        # A separately digest-pinned archive for a different package must
        # not tombstone this still-active selection, even if not installed.
        unrelated = payload(manifest=json.dumps(dict(
            manifest=1, package=dict(id="package.other", version="1.0.0"),
            plugins=[dict(id="plugin.age.other", type="meta.bundle",
                          version="1.0.0", capabilities=["metadata.consolidator"])],
            notice_text_consolidators=[row(plugin_id="plugin.age.other")],
        )))
        with pytest.raises(ValueError, match="archive_selection_mismatch"):
            reconcile_active_descriptive_selection(
                storage, manager, unrelated, expected_generation=1,
                **(args | {"expected_sha256": sha256(unrelated).hexdigest()}),
            )
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
