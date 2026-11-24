"""#4552: trusted composition root and managed retirement revoke a durable pin."""
from hashlib import sha256
from dataclasses import replace

from cifereca.core.runtime import CiferecaCore
from cifereca.core.notice_descriptive_selection_store import (
    read_descriptive_selection_txn, set_descriptive_selection,
)
from tests.test_core_p1_4552_consolidator_archive import payload


def read(core, scope="fr:editions"):
    with core.storage._lock, core.storage._connect() as db:
        db.execute("BEGIN")
        return read_descriptive_selection_txn(db, canonical_type="age", scope=scope)


def test_managed_disable_revokes_exact_pin_without_reselecting_on_enable(tmp_path):
    options = dict(config_path=tmp_path / "config.json",
                   database_path=tmp_path / "library.sqlite",
                   builtin_ui=False, activate_plugins=False, diagnostics_enabled=False)
    data = payload()
    archive = tmp_path / "package.zip"
    archive.write_bytes(data)
    with CiferecaCore.open(**options) as core:
        core.plugins.installer.install(archive)
        core.plugins.refresh()
        pin = core.configure_notice_consolidator(
            data, expected_sha256=sha256(data).hexdigest(), canonical_type="age",
            scope="fr:editions", selected_plugin_id="plugin.age.alpha",
            selected_version="1.0.0", expected_generation=0,
        )
        assert pin == read(core) and pin.schema_sha256
        unrelated = set_descriptive_selection(
            core.storage, canonical_type="age", scope="other:editions",
            selection=replace(pin.selection, scope="other:editions", plugin_id="plugin.other"),
            installed_source_sha256=pin.installed_source_sha256,
            expected_generation=0, schema_sha256=pin.schema_sha256,
        )
        core.plugins.set_enabled("plugin.age.alpha", False)
        revoked = read(core)
        assert revoked.generation == pin.generation + 1
        assert revoked.selection is None and revoked.schema_sha256 is None
        assert read(core, "other:editions") == unrelated
        core.plugins.set_enabled("plugin.age.alpha", True)
        assert read(core) == revoked
        assert core.storage.entitlements() == core.storage.acquisitions() == []
    with CiferecaCore.open(**options) as core:
        assert read(core) == revoked
        assert read(core, "other:editions") == unrelated
