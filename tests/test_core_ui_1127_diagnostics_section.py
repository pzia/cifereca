"""P0 #1127: Diagnostics Gtk section is presentation-only and headless-importable."""
from __future__ import annotations
from tests.settings_source import settings_source_text

import ast
from pathlib import Path
from types import SimpleNamespace
from cifereca.gui.settings_diagnostics_section import build_settings_diagnostics_section


class Widget:
    def __init__(self, label=None, xalign=None, orientation=None, spacing=None, source=None):
        self.label, self.source = label, source
        self.title = self.tooltip = None
        self.visible = True
        self.wrap = False
        self.children = []
        self.signals = []
        self.props = {}
    def set_title(self, value): self.title = value
    def set_wrap(self, value): self.wrap = value
    def set_hexpand(self, value): self.props["hexpand"] = value
    def set_tooltip_text(self, value): self.tooltip = value
    def set_halign(self, value): self.props["halign"] = value
    def set_editable(self, value): self.props["editable"] = value
    def set_monospace(self, value): self.props["monospace"] = value
    def set_wrap_mode(self, value): self.props["wrap"] = value
    def set_top_margin(self, value): self.props["top"] = value
    def set_bottom_margin(self, value): self.props["bottom"] = value
    def set_left_margin(self, value): self.props["left"] = value
    def set_right_margin(self, value): self.props["right"] = value
    def set_min_content_height(self, value): self.props["min_height"] = value
    def set_child(self, value): self.children = [value]
    def set_visible(self, value): self.visible = value
    def add(self, child): self.children.append(child)
    def append(self, child): self.children.append(child)
    def connect(self, *args): self.signals.append(args)


class DropDown(Widget):
    @classmethod
    def new_from_strings(cls, values):
        instance = cls()
        instance.values = list(values)
        return instance


def _render():
    section = Widget()
    calls = []
    def action_button(label, icon, **kwargs):
        calls.append((label, icon, kwargs))
        return Widget(label=label)
    gtk = SimpleNamespace(
        Label=Widget, Box=Widget, DropDown=DropDown,
        ToggleButton=Widget, TextView=Widget, ScrolledWindow=Widget,
        Align=SimpleNamespace(START="start"),
        Orientation=SimpleNamespace(HORIZONTAL="horizontal", VERTICAL="vertical"),
        WrapMode=SimpleNamespace(WORD_CHAR="word_char"),
    )
    ui = build_settings_diagnostics_section(
        section=section, Gtk=gtk,
        Adw=SimpleNamespace(PreferencesGroup=Widget),
        adaptive_action_bar=Widget, action_button=action_button,
    )
    return ui, section, calls


def test_diagnostic_entry_and_core_fallback_preserve_controls():
    ui, section, calls = _render()
    assert len(section.children) == 4
    assert section.children[0] is ui.entry
    assert ui.entry.children == [ui.open_page]
    group = section.children[1]
    assert group.title == "Diagnostic système"
    assert len(group.children) == 5
    assert group.children[0] is ui.summary
    assert group.children[2] is ui.issues_box
    assert group.children[3] is ui.details_toggle
    assert group.children[4] is ui.scroll
    assert ui.fallback_widgets == tuple(section.children[1:])
    assert ui.filter.values == ["À examiner", "Tous"]
    assert ui.filter.tooltip == "Afficher uniquement les anomalies ou l’inventaire complet"
    assert ui.summary.wrap is True
    perf = section.children[2]
    assert perf.title == "Performances · capture volontaire"
    assert "ne démarre qu’après votre action" in perf.children[0].label
    assert perf.children[1] is ui.performance_status
    assert perf.children[2].children == [ui.performance_capture, ui.performance_export]
    assert [row[0] for row in calls] == [
        "Ouvrir Diagnostics",
        "Démarrer la capture détaillée", "Exporter les mesures JSON",
        "Actualiser", "Copier dans le presse-papiers", "Exporter l’affichage", "Effacer l’affichage",
    ]
    assert calls[4][2] == {"suggested": True}
    assert ui.export.tooltip.startswith("Sauvegarde locale de la vue")


def test_long_report_keeps_readonly_scroll_and_original_margins():
    ui, _, _ = _render()
    assert ui.scroll.visible is False
    assert ui.scroll.props["min_height"] == 330
    assert ui.scroll.children == [ui.text]
    assert ui.text.props["editable"] is False
    assert ui.text.props["monospace"] is True
    assert ui.text.props["wrap"] == "word_char"
    for edge in ("top", "bottom", "left", "right"):
        assert ui.text.props[edge] == 10


def test_builder_never_attaches_signals_or_accesses_diagnostic_backend():
    ui, section, _ = _render()
    def walk(w):
        yield w
        for child in w.children:
            yield from walk(child)
    assert all(not row.signals for row in walk(section))
    assert ui.details_toggle.label == "Afficher les détails techniques"


def test_host_and_action_adapter_keep_canonical_copy_export_navigation_and_refresh():
    source = (Path(__file__).resolve().parents[1] / "src/cifereca/gui/main_window.py").read_text("utf-8")
    rendered = ast.unparse(ast.parse(settings_source_text()))
    assert "build_settings_diagnostics_section(" in rendered
    assert "settings_diagnostics_refresh_controller.refresh_settings_diagnostics(" in rendered
    owner = Path(__file__).resolve().parents[1] / "src/cifereca/gui/settings_diagnostics_refresh_controller.py"
    owner_source = ast.unparse(ast.parse(owner.read_text("utf-8")))
    assert "diagnostic_plugin_rows(data, attention_only=attention_only)" in owner_source
    assert "_canonical_settings_report(window.service, version" in owner_source
    assert "self.navigate('plugins')" in rendered
    assert "diag_scroll.set_visible(visible)" in rendered
    assert "build_settings_diagnostic_callbacks(" in rendered
    assert "copy.connect('clicked', copy_text)" in rendered
    assert "export.connect('clicked', export_text)" in rendered
    actions = (Path(__file__).resolve().parents[1] / "src/cifereca/gui/settings_diagnostic_actions.py").read_text("utf-8")
    action_source = ast.unparse(ast.parse(actions))
    assert "Gtk.FileChooserAction.SAVE" in action_source
    assert "Gdk.ContentProvider.new_for_value(content)" in action_source
    assert "self.service.save_diagnostic_preferences(" in action_source
