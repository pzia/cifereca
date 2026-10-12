"""GTK contract for P0 #1012: single redacted snapshot in every detail mode."""
from pathlib import Path


PAGE = Path(__file__).resolve().parents[1] / "cifereca_diagnostics_page.py"


def test_details_are_expanders_with_independent_scroll_and_mode_selector():
    src = PAGE.read_text(encoding="utf-8")
    assert 'Gtk.Expander(label="Détails expurgés")' in src
    assert '["Lisible", "JSON indenté", "Brut expurgé"]' in src
    assert "detail_scroll.set_min_content_height(160)" in src
    assert "detail_view.set_editable(False)" in src
    assert "detail_view.set_monospace(True)" in src


def test_clipboard_and_labels_cannot_bypass_redacted_snapshot():
    src = PAGE.read_text(encoding="utf-8")
    assert "safe = redacted_detail(value)" in src
    assert "format_diagnostic_detail(safe, modes[selected])" in src
    assert "display.get_clipboard().set(visible_text())" in src
    assert 'if "detail" in row:' in src
    assert 'add_detail_view(card, row["detail"])' in src
    assert "detail_label = Gtk.Label(label=_safe_text(detail)" not in src
    assert "cifereca.diagnostics" not in src
    assert "cifereca.gui" not in src


def test_active_checks_and_runtime_diagnostics_use_same_formatter():
    src = PAGE.read_text(encoding="utf-8")
    assert "def add_detail_view(container, value:" in src
    assert 'add_detail_view(card, row["detail"])' in src
    assert "add_detail_view(card, value)" in src


def test_d07_e2e_modes_and_copy_use_real_xtest_keyboard():
    """#1012: the GTK acceptance gate must not fake mode changes/copy calls."""
    source = (Path(__file__).resolve().parents[4] / "e2e" /
              "diagnostics_structured_probe.py").read_text(encoding="utf-8")
    assert 'for key in ("space", *keys, "Return"):' in source
    assert 'choose_mode_keyboard(' in source
    assert "physical(key)" in source
    assert "def copy_json_button(view):" in source
    assert 'physical("space")' in source
    assert "assert copied == expected" in source
    assert "json.loads(copied)" in source
    assert "D07_json_copy_button_keyboard_xtest_exact" in source
    assert "D07_json_mode_selected_by_real_keyboard_xtest" in source
    assert "D07_raw_mode_selected_by_real_keyboard_xtest" in source
    assert ".set_selected(" not in source


def test_json_file_export_uses_same_safe_detail_snapshot_and_local_async_writer():
    src = PAGE.read_text(encoding="utf-8")
    assert 'Gtk.Button(label="Enregistrer JSON…")' in src
    assert "lambda: safe" in src
    assert "prepare_bytes=diagnostic_json_bytes" in src
    assert "filename=\"cifereca-diagnostic.json\"" in src
    assert 'save_json.connect("clicked", json_export.choose)' in src
    assert 'expander.connect("unmap", on_detail_unmap)' in src
    assert "json_export.close()" in src
    assert "self._support_exports.append(json_export)" in src
    assert "self._support_exports.remove(json_export)" in src
