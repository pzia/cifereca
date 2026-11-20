"""#7100 default desktop composition, no native route resurrection."""
from cifereca.builtin_ui.pages import Pages
from cifereca.core import CiferecaCore


def test_desktop_capability_does_not_create_native_route(tmp_path):
    with CiferecaCore.open(config_path=tmp_path / "config.json",
                          database_path=tmp_path / "db.sqlite",
                          activate_plugins=False) as core:
        assert "maintenance.page.host" in core.brokers
        assert not any(page.route == "tools" for page in Pages().pages())
        assert core.plugins.service("ui.page.maintenance") is None
        assert not any(page.route == "tools" for page in core.plugins.page_contributions())


def test_headless_has_no_ui_mount_capability(tmp_path):
    with CiferecaCore.open(config_path=tmp_path / "config.json",
                          database_path=tmp_path / "db.sqlite",
                          builtin_ui=False, activate_plugins=False) as core:
        assert "maintenance.page.host" not in core.brokers
        assert core.plugins.service("ui.page.maintenance") is None



def test_runtime_real_packages_are_independently_optional(tmp_path):
    from tests.test_core_p0_7100_installed_maintenance_packages import PAGE, TOOLS, archive
    from cifereca.plugin_api.guided_maintenance import GuidedMaintenanceTool

    with CiferecaCore.open(config_path=tmp_path / "config.json",
                          database_path=tmp_path / "db.sqlite",
                          activate_plugins=False) as core:
        manager = core.plugins
        assert all(spec[3] in core.brokers for spec in TOOLS)
        for spec in (PAGE, *TOOLS):
            manager.install(archive(tmp_path, spec))
        keys = {spec[2] for spec in TOOLS}

        def installed_tools():
            return {row.plugin_id for row in manager.services("maintenance.tool")} & keys

        assert installed_tools() == keys
        assert sum(page.route == "tools" for page in manager.page_contributions()) == 1
        for spec in TOOLS:
            key = spec[2]
            tool = manager.service(key)
            assert isinstance(tool, GuidedMaintenanceTool)
            manager.set_enabled(key, False)
            assert installed_tools() == keys - {key}
            assert manager.service(key) is None
            manager.set_enabled(key, True)
            assert installed_tools() == keys
            manager.remove_package(spec[1])
            assert manager.service(key) is None
            manager.install(archive(tmp_path, spec))
            assert installed_tools() == keys
        manager.set_enabled(PAGE[2], False)
        assert not any(page.route == "tools" for page in manager.page_contributions())
        assert installed_tools() == keys
