"""P0 #1127 — headless behavior of identities/accounts GTK skeleton."""
from __future__ import annotations

from types import SimpleNamespace as NS

from cifereca.gui.settings_accounts_section import (
    build_identity_settings_header,
    build_provider_accounts_header,
    build_provider_account_buttons,
)


class Widget:
    def __init__(self, *args, label="", orientation=None, spacing=None, xalign=None):
        self.label = label
        self.children = []
        self.wrap = False
        self.tooltip = None
        self.connections = []
        self.spacing = spacing

    def set_wrap(self, value): self.wrap = value
    def set_tooltip_text(self, text): self.tooltip = text
    def append(self, widget): self.children.append(widget)
    def connect(self, *args): self.connections.append(args)


def environment():
    classes = NS(Label=Widget, Button=Widget, Box=Widget, Orientation=NS(VERTICAL=1, HORIZONTAL=0))
    marks = []
    def css(widget, *names): marks.append((widget, names))
    return classes, marks


def test_identity_header_order_and_security_warning_are_preserved():
    Gtk, marks = environment()
    section = Widget()
    target = build_identity_settings_header(section=section, Gtk=Gtk, add_css=lambda w,*c: marks.append((w,c)))
    assert target is section.children[-1]
    assert len(section.children) == 4
    assert "stockés en clair" in section.children[0].label
    assert "jamais inclus dans les diagnostics" in section.children[0].label
    assert section.children[1].label == "Identités"
    assert "Kobo-Adept" in section.children[2].label
    assert section.children[2].wrap is True
    assert target.spacing == 14
    assert all(not x.connections for x in section.children)


def test_accounts_intro_has_no_plugin_capability_inventory_or_direct_navigation():
    Gtk, marks = environment()
    section = Widget()
    header = build_provider_accounts_header(section=section, Gtk=Gtk, add_css=lambda w,*c: marks.append((w,c)))
    assert len(section.children) == 4
    assert section.children[0] is header.plugins_button
    assert header.plugins_button.label == "Gérer les plugins et leurs capacités techniques…"
    assert header.plugins_button.connections == []
    assert section.children[1].label == "Sources et comptes fournisseurs"
    assert "Les capacités internes des plugins restent dans Plugins et Diagnostic." in section.children[2].label
    assert section.children[3] is header.accounts_container
    assert header.accounts_container.spacing == 14


def test_buttons_only_offer_ready_non_public_descriptors_and_do_not_bind_actions():
    Gtk, _ = environment()
    section = Widget()
    descriptors = [
        NS(id="paris", display_name="Paris", state="ready", credential_fields=["card"]),
        NS(id="gallica", display_name="Gallica", state="ready", credential_fields=[]),
        NS(id="internal", display_name="Public", state="ready", credential_fields=[]),
        NS(id="broken", display_name="Inaccessible", state="failed", credential_fields=["pass"]),
    ]
    buttons = build_provider_account_buttons(section=section, descriptors=descriptors, public_ids={"internal"}, Gtk=Gtk)
    assert [(button.label, key) for button,key in buttons] == [
        ("Ajouter un compte Paris", "paris"),
        ("Ajouter la source Gallica", "gallica"),
    ]
    assert section.children[-1].children == [button for button,_ in buttons]
    assert all(not button.connections for button,_ in buttons)
