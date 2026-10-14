from tests.settings_source import settings_source_text
from tests.gui_source import gui_source_text
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAIN_WINDOW = ROOT / "src/cifereca/gui/main_window.py"


def test_settings_tabs_stay_compact_without_shortening_labels():
    source = gui_source_text(MAIN_WINDOW)
    shell = gui_source_text(ROOT / "src/cifereca/gui/settings_shell.py")

    assert '("accounts", "Identités et comptes")' in shell
    assert '("sync", "Synchronisation")' in shell
    assert 'switcher.add_css_class("settings-tabs")' in shell
    assert "build_settings_shell(Gtk=Gtk, Adw=Adw, add_css=add_css)" in settings_source_text()
    assert "shell_stylesheet_controller.install_css(" in source
    stylesheet = (ROOT / "src/cifereca/gui/shell_stylesheet_controller.py").read_text(
        encoding="utf-8"
    )
    assert '.settings-tabs button { padding-left: 8px; padding-right: 8px; min-width: 0; }' in stylesheet
