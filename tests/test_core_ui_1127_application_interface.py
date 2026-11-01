"""P0 #1127: app and interface Settings controls preserve original GTK contracts."""
from __future__ import annotations

from types import SimpleNamespace

from cifereca.gui.settings_application_interface import build_settings_application_interface


class Widget:
    def __init__(self, **props):
        self.props = props
        self.title = ""
        self.subtitle = ""
        self.active = None
        self.selected = None
        self.children = []
        self.suffixes = []
        self.calls = []

    def set_title(self, value): self.title = value
    def set_subtitle(self, value): self.subtitle = value
    def set_active(self, value): self.active = value
    def set_selected(self, value): self.selected = value
    def set_valign(self, value): self.calls.append(("valign", value))
    def add_suffix(self, widget): self.suffixes.append(widget)
    def add(self, widget): self.children.append(widget)
    def append(self, widget): self.children.append(widget)
    def connect(self, *_args): raise AssertionError("builders must not own signals")


class DropDown(Widget):
    @classmethod
    def new_from_strings(cls, choices):
        widget = cls()
        widget.options = list(choices)
        return widget


def _build(values=None, *, effective_level=None):
    config = SimpleNamespace(get=lambda key, default=None: (values or {}).get(key, default))
    gtk = SimpleNamespace(Switch=Widget, DropDown=DropDown, Button=Widget,
                          Align=SimpleNamespace(CENTER="center"))
    adw = SimpleNamespace(PreferencesGroup=Widget, ActionRow=Widget)
    diagnostic, interface = Widget(), Widget()
    view = build_settings_application_interface(
        diagnostic_section=diagnostic, interface_section=interface,
        config=config, Gtk=gtk, Adw=adw,
        effective_diagnostics_level=effective_level,
    )
    return view, diagnostic, interface


def test_default_controls_preserve_hierarchy_and_labels():
    view, diagnostic, interface = _build()
    assert len(diagnostic.children) == 1
    group = diagnostic.children[0]
    assert group.title == "Application"
    assert [child.title for child in group.children] == [
        "Niveau de diagnostic",
    ]
    assert len(interface.children) == 1
    assert view.plugins_button is interface.children[0]
    assert view.plugins_button.props["label"] == "Configurer la barre de titre et les actions…"
    assert view.diagnostics_switch is None
    assert not hasattr(view, "experimental_switch")


def test_legacy_experimental_flag_is_not_a_settings_action():
    view, _, _ = _build({
        "diagnostics.enabled": False,
        "experimental_actions": True,
        "diagnostics.level": "detailed",
    })
    assert view.diagnostics_switch is None
    assert not hasattr(view, "experimental_switch")
    assert view.levels == ["errors", "normal", "verbose", "trace"]
    assert view.level_dropdown.options == view.levels
    assert view.level_dropdown.selected == 2


def test_unknown_level_falls_back_to_normal_and_default_switches():
    view, _, _ = _build({"diagnostics.level": "unsupported"})
    assert view.level_dropdown.selected == 1
    assert view.diagnostics_switch is None
    assert not hasattr(view, "experimental_switch")


def test_builder_does_not_connect_widgets_or_persist_configuration():
    values = {"diagnostics.enabled": True}
    view, _, _ = _build(values)
    assert values == {"diagnostics.enabled": True}
    assert view.diagnostics_switch is None


def test_diagnostics_collector_cannot_be_disabled_from_preferences():
    view, diagnostic, _interface = _build({"diagnostics.enabled": False})
    row = diagnostic.children[0].children[0]
    assert row.title == "Niveau de diagnostic"
    assert view.diagnostics_switch is None
    assert view.level_dropdown.selected == 1


def test_effective_runtime_trace_overrides_saved_preference_in_dropdown():
    view, _, _ = _build({"diagnostics.level": "normal"}, effective_level="trace")
    assert view.level_dropdown.selected == 3
    # A CLI override changes the live presentation, not persistent config.
    view, _, _ = _build({"diagnostics.level": "normal"})
    assert view.level_dropdown.selected == 1



def test_settings_builder_does_not_even_read_obsolete_experimental_bit():
    keys = []
    config = SimpleNamespace(get=lambda key, default=None: (
        keys.append(key) or {"diagnostics.level": "normal"}.get(key, default)
    ))
    diag, interface = Widget(), Widget()
    gtk = SimpleNamespace(Switch=Widget, DropDown=DropDown, Button=Widget,
                          Align=SimpleNamespace(CENTER="center"))
    adw = SimpleNamespace(PreferencesGroup=Widget, ActionRow=Widget)
    view = build_settings_application_interface(
        diagnostic_section=diag, interface_section=interface, config=config,
        Gtk=gtk, Adw=adw,
    )
    assert not hasattr(view, "experimental_switch")
    assert "experimental_actions" not in keys
    assert [child.props.get("label") for child in interface.children] == [
        "Configurer la barre de titre et les actions…"
    ]
