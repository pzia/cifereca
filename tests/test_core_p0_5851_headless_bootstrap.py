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


def test_headless_core_does_not_import_external_ui_even_when_forced(tmp_path):
    from cifereca.plugins import PackageInstaller, PackageStore
    from tests.test_cifereca_step_b import _manifest, _zip

    sentinel = tmp_path / "UI-IMPORTED"
    manifest = _manifest("org.test.headless-mixed", "1.0.0", [
        {"id": "test.headless-ui", "type": "ui.page",
         "entrypoint": "headless_forbidden_ui:PLUGIN", "enabled_by_default": True},
        {"id": "test.headless-business", "type": "runtime.adapter",
         "entrypoint": "headless_business_fixture:PLUGIN", "enabled_by_default": True},
        {"id": "test.headless-dependent", "type": "runtime.adapter",
         "entrypoint": "headless_forbidden_dependent:PLUGIN", "enabled_by_default": True,
         "requires": [{"plugin": "test.headless-ui"}]},
    ])
    archive = _zip(tmp_path / "mixed.zip", manifest, {
        "headless_forbidden_ui.py": (
            f"from pathlib import Path\nPath({str(sentinel)!r}).touch()\n"
            "raise AssertionError('headless UI imported')\n"
        ),
        "headless_business_fixture.py": (
            "class Plugin:\n def activate(self, ctx): return 'business-ready'\n"
            " def deactivate(self): pass\nPLUGIN=Plugin()\n"
        ),
        "headless_forbidden_dependent.py": "raise AssertionError('disabled UI dependency imported')\n",
    })
    store = PackageStore(tmp_path / "plugins" / "packages")
    PackageInstaller(store).install(archive, activate=True)
    with CiferecaCore.open(
        config_path=tmp_path / "config.json",
        database_path=tmp_path / "library.sqlite",
        builtin_ui=False,
        force_plugins=("test.headless-ui",),
    ) as core:
        assert not sentinel.exists()
        status = core.plugins.status("test.headless-ui")
        assert status.enabled is False
        assert status.state == "disabled"
        assert status.reason == "headless_ui_excluded"
        assert core.plugins._records["test.headless-ui"].instance is None
        assert core.plugins.status("test.headless-business").state == "ready"
        assert core.plugins._records["test.headless-business"].instance is not None
        assert core.plugins.status("test.headless-dependent").reason == "dependency_disabled"
        core.plugins.activate()
        assert not sentinel.exists()
