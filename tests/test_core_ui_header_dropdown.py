from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAIN_WINDOW = ROOT / "src/cifereca/gui/main_window.py"


def test_header_dropdowns_can_ellipsize_long_labels():
    source = MAIN_WINDOW.with_name("shell_header_controller.py").read_text(encoding="utf-8")

    assert "factory = Gtk.SignalListItemFactory()" in source
    assert "label.set_ellipsize(Pango.EllipsizeMode.END)" in source
    assert "label.set_single_line_mode(True)" in source
    assert 'window.identity_dropdown = compact_dropdown("Identité active")' in source
    assert 'window.account_dropdown = compact_dropdown("Délégations de l’identité active")' in source
    assert "label.set_tooltip_text(text)" in source
    assert "dropdown.set_list_factory(list_factory)" in source
    assert "label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)" in source
    assert "label.set_max_width_chars(44)" in source
