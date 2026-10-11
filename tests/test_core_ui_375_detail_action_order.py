from __future__ import annotations
from tests.gui_source import gui_source_text

import ast
from pathlib import Path
from types import SimpleNamespace as NS


MAIN = Path(__file__).resolve().parents[1] / "src/cifereca/gui/main_window.py"


def _methods(*names):
    tree = ast.parse(gui_source_text(MAIN))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "DetailPanel")
    selected = [node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name in names]
    ns = {"Gtk": NS(Button=object)}
    exec(compile(ast.fix_missing_locations(ast.Module(body=selected, type_ignores=[])), str(MAIN), "exec"), ns)
    return [ns[name] for name in names]


class Button:
    def __init__(self, label, identifier=None):
        self._cifereca_action_label = label
        self._cifereca_action_id = identifier
        self.classes = set()

    def get_label(self):
        return self._cifereca_action_label

    def add_css_class(self, name):
        self.classes.add(name)

    def remove_css_class(self, name):
        self.classes.discard(name)


class Bar:
    def __init__(self):
        self.buttons = []
        self.visible = False

    def append(self, button):
        self.buttons.append(button)

    def clear(self):
        self.buttons.clear()

    def set_visible(self, visible):
        self.visible = visible


def test_detail_action_priority_matches_common_ux_order():
    priority, _actions = _methods("_detail_action_priority", "_actions")
    host = NS(_detail_action_priority=lambda button: priority(button))

    assert priority(Button("Ouvrir", "open-local")) < priority(Button("Voir dans Bibliothèque"))
    assert priority(Button("Voir dans Bibliothèque")) < priority(Button("Disponibilités / téléchargement"))
    assert priority(Button("Disponibilités / téléchargement")) < priority(Button("Envoyer à Alice", "send-local:alice"))


def test_late_primary_action_is_reordered_before_existing_secondary_actions():
    priority, actions = _methods("_detail_action_priority", "_actions")
    bar = Bar()
    host = NS(
        action_scroll=bar,
        _action_ids=set(),
        _action_buttons=[],
        _rendered_action_count=0,
        _detail_action_priority=lambda button: priority(button),
    )

    navigation = Button("Voir dans Bibliothèque")
    availability = Button("Disponibilités / téléchargement")
    online = Button("En ligne")
    actions(host, [navigation, availability, online])

    opening = Button("Ouvrir", "open-local")
    transfer = Button("Envoyer à Alice", "send-local:alice")
    actions(host, [opening, transfer])

    assert bar.buttons == [opening, navigation, availability, online, transfer]
    assert opening.classes == {"suggested-action"}
    assert all("suggested-action" not in button.classes for button in bar.buttons[1:])


def test_duplicate_action_id_does_not_change_order_or_add_second_primary_style():
    priority, actions = _methods("_detail_action_priority", "_actions")
    bar = Bar()
    host = NS(
        action_scroll=bar,
        _action_ids=set(),
        _action_buttons=[],
        _rendered_action_count=0,
        _detail_action_priority=lambda button: priority(button),
    )
    opening = Button("Ouvrir", "open-local")
    actions(host, [Button("En ligne"), opening])
    actions(host, [Button("Ouvrir", "open-local")])

    assert [button._cifereca_action_label for button in bar.buttons] == ["Ouvrir", "En ligne"]
    assert opening.classes == {"suggested-action"}
