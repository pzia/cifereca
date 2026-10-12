"""#1127: action bars remain paired and defer persistence to MainWindow."""
from __future__ import annotations

from types import SimpleNamespace

from cifereca.gui.settings_action_bars import build_settings_action_bars


class Box:
    def __init__(self, source=None):
        self.source = source
        self.children = []
        self.alignment = None

    def set_halign(self, alignment):
        self.alignment = alignment

    def append(self, child):
        self.children.append(child)


def test_actions_keep_labels_order_icons_and_two_paired_locations():
    root, heading = Box(), Box()
    created = []
    mirrored = []

    def button(label, icon, **kwargs):
        widget = SimpleNamespace(label=label, icon=icon, options=kwargs)
        created.append(widget)
        return widget

    actions = build_settings_action_bars(
        root=root, heading=heading, adaptive_action_bar=Box,
        action_button=button, mirror_sensitivity=lambda a, b: mirrored.append((a, b)),
        Gtk=SimpleNamespace(Align=SimpleNamespace(END="end")),
    )
    assert heading.children == [actions.heading_bar]
    assert root.children == [actions.footer_bar]
    assert actions.heading_bar.children == [actions.save, actions.reconnect]
    assert actions.footer_bar.children == [actions.save_bottom, actions.reconnect_bottom]
    assert actions.heading_bar.alignment == actions.footer_bar.alignment == "end"
    assert [item.label for item in created] == ["Enregistrer", "Reconnecter", "Enregistrer", "Reconnecter"]
    assert [item.icon for item in created] == [
        "document-save-symbolic", "network-transmit-receive-symbolic",
        "document-save-symbolic", "network-transmit-receive-symbolic",
    ]
    assert created[0].options["suggested"] is True
    assert created[2].options["suggested"] is True
    assert mirrored == [(actions.save, actions.save_bottom),
                        (actions.reconnect, actions.reconnect_bottom)]


def test_builder_only_creates_widgets_with_no_save_or_reconnect_side_effect():
    callbacks = []
    actions = build_settings_action_bars(
        root=Box(), heading=Box(), adaptive_action_bar=Box,
        action_button=lambda label, icon, **kwargs: SimpleNamespace(label=label),
        mirror_sensitivity=lambda a, b: callbacks.append(("mirror", a, b)),
        Gtk=SimpleNamespace(Align=SimpleNamespace(END="end")),
    )
    assert len(callbacks) == 2
    assert actions.save.label == "Enregistrer"
    assert actions.reconnect.label == "Reconnecter"
