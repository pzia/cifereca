from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAIN_WINDOW = ROOT / "src/cifereca/gui/main_window.py"


def test_header_dropdowns_can_ellipsize_long_labels():
    source = MAIN_WINDOW.read_text()

    assert "factory = Gtk.SignalListItemFactory()" in source
    assert "label.set_ellipsize(Pango.EllipsizeMode.END)" in source
    assert "label.set_single_line_mode(True)" in source
    assert 'self.identity_dropdown = compact_dropdown("Identité active")' in source
    assert 'self.account_dropdown = compact_dropdown("Délégations de l’identité active")' in source
    assert "label.set_tooltip_text(text)" in source
