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
