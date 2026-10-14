"""CoreUI P0 #1127: identity/ADEPT GTK presentation without any side effect."""
from __future__ import annotations
from tests.settings_source import settings_source_text

import ast
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

from cifereca.gui.settings_identity_editor import build_identity_settings_editor


class Widget:
    def __init__(self, label=None, **_kwargs):
        self.title = None
        self.text = None
        self.subtitle = None
        self.tooltip = None
        self.active = None
        self.sensitive = True
        self.visible = True
        self.children = []
        self.suffixes = []
        self.classes = []
        self.label = label
        self.signals = []

    def set_title(self, value): self.title = value
    def set_text(self, value): self.text = value
    def set_subtitle(self, value): self.subtitle = value
    def set_tooltip_text(self, value): self.tooltip = value
    def set_active(self, value): self.active = value
    def set_sensitive(self, value): self.sensitive = value
    def set_visible(self, value): self.visible = value
    def set_valign(self, _value): pass
    def add(self, value): self.children.append(value)
    def append(self, value): self.children.append(value)
    def add_suffix(self, value): self.suffixes.append(value)
    def add_css_class(self, value): self.classes.append(value)
    def connect(self, name, callback): self.signals.append((name, callback))


class Button(Widget):
    @classmethod
    def new_from_icon_name(cls, name):
        value = cls()
        value.icon = name
        return value


def _build(data=None, *, identity_id="alice",
           adept_profile="alice-profile", normal_subtitle="État local ADEPT"):
    container = Widget()
    gtk = SimpleNamespace(Button=Button, Switch=Widget,
                          Align=SimpleNamespace(CENTER="center"))
    adw = SimpleNamespace(PreferencesGroup=Widget, EntryRow=Widget,
                          ActionRow=Widget)
    controls = build_identity_settings_editor(
        data=data or {}, identity_id=identity_id,
        adept_profile=adept_profile, normal_subtitle=normal_subtitle,
        identities_container=container, Gtk=gtk, Adw=adw,
    )
    return controls, container


def test_existing_identity_preserves_exact_rows_order_and_readonly_id():
    data = {
        "id": "alice", "label": "Alice", "directory_name": "MesLivres",
        "preferences": {
            "reading_languages": ["fr", "en"], "prefer_original_language": True,
        },
    }
    (editor, directory, adept, delete), container = _build(data)
    assert len(container.children) == 1
    group = container.children[0]
    assert group.title == "Alice"
    assert [row.title for row in group.children] == [
        "Identifiant stable", "Nom affiché",
        "Nom du répertoire bibliothèque",
        "Langues de lecture (ordre, séparées par des virgules)",
        "Préférer la langue originale",
        "Profil ADE lié à cette identité (optionnel)",
        "Parcours normal · Paris principal et ADEPT",
        "Supprimer cette identité de la configuration",
    ]
    assert editor["id"].text == "alice"
    assert editor["id"].sensitive is False
    assert editor["directory"].text == "MesLivres"
    assert editor["languages"].text == "fr, en"
    assert editor["prefer_vo"].active is True
    assert editor["adept_profile"].text == "alice-profile"
    assert group.children[6].subtitle == "État local ADEPT"
    assert directory.icon == "folder-open-symbolic"
    assert directory in editor["directory"].suffixes
    assert adept in editor["adept_profile"].suffixes
    assert delete in group.children[-1].suffixes
    assert "destructive-action" in delete.classes


def test_unsaved_identity_remains_editable_and_not_automatically_bound():
    (editor, _, _, _), group = _build({}, identity_id="identite", adept_profile="",
                                      normal_subtitle="À configurer")
    assert editor["id"].sensitive is True
    assert editor["id"].text == "identite"
    assert editor["directory"].text == "identite"
    assert editor["languages"].text == "fr"
    assert editor["prefer_vo"].active is False
    assert editor["adept_profile"].text == ""
    assert group.children[0].children[6].subtitle == "À configurer"


def test_adept_status_is_only_rendered_not_inferred_or_refreshed():
    text = "À vérifier · compte Paris principal absent."
    (editor, _, _, _), container = _build(
        {"id": "alice", "adept_profile": "unsaved-change"},
        adept_profile="locally-bound-profile", normal_subtitle=text,
    )
    assert editor["adept_profile"].text == "locally-bound-profile"
    assert container.children[0].children[6].subtitle == text
    assert "sans test Adobe" in container.children[0].children[6].tooltip


def test_builder_does_not_connect_callbacks_mutate_input_or_persist():
    data = {
        "id": "alice", "preferences": {"reading_languages": ["fr", "en"]},
        "adept_profile": "manual-historical",
    }
    initial = deepcopy(data)
    (editor, directory, adept, delete), group = _build(data)
    assert data == initial
    assert editor["deleted"] is False
    assert not any(control.signals for control in (directory, adept, delete))
    assert len(group.children) == 1


def test_window_keeps_unique_identity_ids_exact_bindings_and_callback_ownership():
    source = (
        Path(__file__).resolve().parents[1] / "src/cifereca/gui/main_window.py"
    ).read_text(encoding="utf-8")
    body = ast.unparse(ast.parse(settings_source_text()))
    assert "while identity_id in existing_ids:" in body
    assert "self.service.storage.adept_profile_binding_get(" in body
    assert "self.service.normal_paris_adept_status(identity_id)" in body
    assert "build_identity_settings_editor(" in body
    assert "self._choose_identity_directory(" in body
    assert "self._choose_adept_profile(entry, button)" in body
    assert "mark_settings_dirty('accounts')" in body
    assert "SettingsCommitController(" in body
    assert "settings_committer.submit(" in body
