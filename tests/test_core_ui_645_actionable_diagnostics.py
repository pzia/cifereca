from __future__ import annotations
from tests.gui_source import gui_source_text
from tests.settings_source import settings_source_text

from pathlib import Path

from cifereca.gui.diagnostic_presentation import (
    diagnostic_attention_rows,
    diagnostic_plugin_rows,
)


ROOT = Path(__file__).resolve().parents[1]


def _status():
    return {
        "ready": 2,
        "attention": 3,
        "plugins": [
            {"plugin_id": "ok", "display_name": "OK", "state": "ready", "enabled": True},
            {"plugin_id": "blocked", "display_name": "Bloqué", "state": "blocked", "enabled": True, "reason_code": "missing_dependency"},
            {"plugin_id": "error", "display_name": "Erreur", "state": "error", "enabled": True},
            {"plugin_id": "incompatible", "display_name": "Incompatible", "state": "incompatible", "enabled": True},
            {"plugin_id": "off", "display_name": "Désactivé", "state": "disabled", "enabled": False},
        ],
    }


def test_attention_projection_matches_shell_counter_exactly():
    data = _status()
    rows = diagnostic_attention_rows(data)
    assert len(rows) == data["attention"]
    assert {row["plugin_id"] for row in rows} == {"blocked", "error", "incompatible"}
    assert all(row["attention"] for row in rows)


def test_all_filter_keeps_anomalies_first():
    rows = diagnostic_plugin_rows(_status(), attention_only=False)
    assert [row["plugin_id"] for row in rows[:3]] == ["blocked", "error", "incompatible"]
    assert not rows[-1]["attention"]


def test_attention_rows_have_user_summary_and_bounded_technical_details():
    row = next(row for row in diagnostic_attention_rows(_status()) if row["plugin_id"] == "blocked")
    assert "bloqué" in row["summary"].casefold()
    assert "missing_dependency" in row["technical"]


def test_settings_diagnostic_is_actionable_and_raw_details_are_collapsed():
    source = settings_source_text()
    section = gui_source_text(ROOT / "src/cifereca/gui/settings_diagnostics_section.py")
    assert 'Gtk.DropDown.new_from_strings(["À examiner", "Tous"])' in section
    assert 'diag_scroll.set_visible(False)' in section
    refresh = gui_source_text(ROOT / "src/cifereca/gui/settings_diagnostics_refresh_controller.py")
    assert 'label="Ouvrir Plugins"' in refresh
    assert 'open_diagnostic_plugin(button, pid)' in refresh
    assert 'focus(plugin_id)' in source
    assert 'Copier dans le presse-papiers' in section
    assert 'Exporter l’affichage' in section
    actions = gui_source_text(ROOT / "src/cifereca/gui/settings_diagnostic_actions.py")
    assert 'build_settings_diagnostic_callbacks(' in source
    assert 'Gtk.FileChooserAction.SAVE' in actions


def test_fallback_system_page_uses_same_attention_projection_and_copy_action():
    source = gui_source_text(ROOT / "src/cifereca/builtin_ui/system_page.py")
    assert "diagnostic_plugin_rows" in source
    assert 'Gtk.DropDown.new_from_strings(["À examiner", "Tous"])' in source
    assert 'action_button("Copier"' in source
    assert 'action_button("Exporter"' in source
    assert 'Gtk.FileChooserAction.SAVE' in source
    assert 'open_plugin(pid)' in source


def test_plugin_manager_can_focus_a_diagnostic_target_without_mutating_activation():
    source = gui_source_text(ROOT / "src/cifereca/gui/plugin_manager_page.py")
    assert "def focus_plugin(self, plugin_id: str)" in source
    assert 'self._view_query = str(plugin_id or "").strip()' in source
    assert 'self._view_filter = "all"' in source
