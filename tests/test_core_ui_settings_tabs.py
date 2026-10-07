from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAIN_WINDOW = ROOT / "src/cifereca/gui/main_window.py"


def test_settings_tabs_stay_compact_without_shortening_labels():
    source = MAIN_WINDOW.read_text()

    assert '("accounts", "Identités et comptes")' in source
    assert '("sync", "Synchronisation")' in source
    assert 'switcher.add_css_class("settings-tabs")' in source
    assert '.settings-tabs button { padding-left: 8px; padding-right: 8px; min-width: 0; }' in source
