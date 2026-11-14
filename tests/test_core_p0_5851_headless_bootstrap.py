"""#5851: Core composition may omit the built-in UI without a GTK bootstrap.

These checks exercise the real stores and plugin discovery, with no remote
provider and no application/window construction.
"""
from cifereca.core import CiferecaCore


def test_core_headless_activation_omits_builtin_ui_package(tmp_path):
    with CiferecaCore.open(
        config_path=tmp_path / "config.json",
        database_path=tmp_path / "library.sqlite",
        builtin_ui=False,
    ) as core:
        assert core.plugins._builtin_ui is False
        assert not any(
            record.package.sha256 == "builtin"
            for record in core.plugins._records.values()
        )
        assert core.library is not None
        assert core.storage.schema_version() >= 1


def test_core_default_retains_gui_package_discovery(tmp_path):
    with CiferecaCore.open(
        config_path=tmp_path / "config.json",
        database_path=tmp_path / "library.sqlite",
        activate_plugins=False,
    ) as core:
        assert core.plugins._builtin_ui is True
        core.plugins.refresh()
        assert any(
            record.package.sha256 == "builtin"
            for record in core.plugins._records.values()
        )
