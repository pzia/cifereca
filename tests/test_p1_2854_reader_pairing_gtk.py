"""P1 #2854: explicit USB pairing and activation never attest physical presence.

Fake GTK/Adw only; real SQLite with two profiles and independent device IDs.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cifereca.gui.reader_pairing_actions import (
    request_reader_pairing, toggle_reader_examination,
)
from tests.test_p1_2854_readers_master_detail_page import environment


class Field:
    def __init__(self, **kwargs):
        self.value = ""
        self.visible = True
        self.handlers = {}

    def set_placeholder_text(self, value):
        self.placeholder = value

    def set_max_length(self, limit):
        self.limit = limit

    def set_text(self, value):
        self.value = value

    def get_text(self):
        return self.value

    def set_visible(self, value):
        self.visible = value

    def connect(self, signal, callback):
        self.handlers[signal] = callback

    def set_hexpand(self, value):
        self.hexpand = value

    def set_tooltip_text(self, value):
        self.tooltip = value


class Group:
    def __init__(self, **kwargs):
        self.children = []

    def append(self, child):
        self.children.append(child)


class Text(Field):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.text = kwargs.get("label", "")

    def set_label(self, value):
        self.text = value

    def set_wrap(self, value):
        self.wrap = value


class Button(Field):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.text = kwargs.get("label", "")

    def click(self):
        self.handlers["clicked"](self)


class Check(Field):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.active = False

    def get_active(self):
        return self.active

    def set_active(self, value):
        if self.active != value:
            self.active = value
            if "toggled" in self.handlers:
                self.handlers["toggled"](self)


class Drop(Field):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.selection = 0
        self.sensitive = True
        self.model = []

    @classmethod
    def new_from_strings(cls, labels):
        widget = cls()
        widget.model = list(labels)
        return widget

    def set_model(self, model):
        self.model = list(model)

    def set_selected(self, position):
        self.selection = position
        if "notify::selected" in self.handlers:
            self.handlers["notify::selected"](self, None)

    def get_selected(self):
        return self.selection

    def set_sensitive(self, value):
        self.sensitive = value


class Dialog:
    def __init__(self, title, description):
        self.title = title
        self.description = description
        self.handlers = {}
        self.responses = {}
        self.extra = None
        self.presented = False

    @classmethod
    def new(cls, owner, title, description):
        assert owner is not None
        return cls(title, description)

    def set_extra_child(self, widget):
        self.extra = widget

    def add_response(self, key, label):
        self.responses[key] = label

    def set_close_response(self, key):
        self.close_response = key

    def connect(self, name, handler):
        self.handlers[name] = handler

    def present(self):
        self.presented = True

    def respond(self, code):
        self.handlers["response"](self, code)

    @property
    def name_field(self):
        return self.extra.children[5]

    @property
    def uuid_field(self):
        return self.extra.children[8]

    @property
    def expert_field(self):
        return self.extra.children[6]

    @property
    def volume_choice(self):
        return self.extra.children[1]

    @property
    def refresh_button(self):
        return self.extra.children[2]

    @property
    def volume_status(self):
        return self.extra.children[3]


GUI = NS(
    Orientation=NS(VERTICAL=1), Box=Group, Label=Text, Entry=Field,
    DropDown=Drop, Button=Button, CheckButton=Check,
    StringList=NS(new=lambda values: list(values)),
)
ADW = NS(MessageDialog=Dialog)


def rig(tmp_path):
    db, entries, window, action_calls = environment(tmp_path)
    window._account_context_generation = 0
    window._route_display_token = object()
    errors = []
    window.show_error = lambda msg, title: errors.append((msg, title))
    window._render_readers()
    return db, window, errors


def open_dialog(window, *, expert=True):
    request_reader_pairing(window, Gtk=GUI, Adw=ADW)
    dialog = getattr(window, "_reader_pairing_dialog", None)
    assert dialog is not None and dialog.presented
    assert "présence" in dialog.description
    assert dialog.responses == {"cancel": "Annuler", "confirm": "Associer"}
    assert dialog.volume_choice.get_selected() == 0xFFFFFFFF
    assert not dialog.uuid_field.visible
    if expert:
        # Existing tests exercise the preserved, explicitly selected expert
        # workflow. New #6041 tests check the default chooser separately.
        dialog.expert_field.set_active(True)
        assert dialog.uuid_field.visible
    return dialog


def test_pairing_is_not_written_until_explicit_confirm(tmp_path):
    db, window, errors = rig(tmp_path)
    before = deepcopy(db.device_bindings())
    dialog = open_dialog(window)
    assert db.device_bindings() == before
    dialog.name_field.value = "Ma liseuse"
    dialog.uuid_field.value = "uuid-alice-9001"
    dialog.respond("cancel")
    assert db.device_bindings() == before
    assert window._reader_pairing_dialog is None
    assert not errors


def test_confirm_declares_disabled_volume_without_scan_or_file_copy(tmp_path):
    db, window, errors = rig(tmp_path)
    before = deepcopy(db.device_placements())
    dialog = open_dialog(window)
    dialog.name_field.value = "Kobo USB"
    dialog.uuid_field.value = "uuid-alice-9002"
    dialog.respond("confirm")
    selected_id = window._reader_selected_device_id
    binding = db.device_binding_get(selected_id)
    assert binding["identity_id"] == "alice"
    assert binding["label"] == "Kobo USB"
    assert binding["metadata"]["usb_fingerprint"] == "uuid-alice-9002"
    assert binding["target_directory"] == "Books/Cifereca"
    assert binding["enabled"] is False
    assert db.device_placements() == before
    assert "support non vérifié" in window._reader_status_label.label
    assert window._reader_activation_button.label == "Activer examens USB"
    assert window._reader_activation_button.sensitive is True
    assert not errors


def test_separate_toggle_changes_only_setting_and_can_disable_again(tmp_path):
    db, window, errors = rig(tmp_path)
    dialog = open_dialog(window)
    dialog.name_field.value = "Reader"
    dialog.uuid_field.value = "uuid-alice-9003"
    dialog.respond("confirm")
    before = deepcopy(db.device_placements())
    toggle = window._reader_activation_button
    assert toggle.label == "Activer examens USB"
    toggle.callbacks["clicked"](toggle)
    assert db.device_binding_get(window._reader_selected_device_id)["enabled"] is True
    assert toggle.label == "Désactiver examens USB"
    assert db.device_placements() == before
    assert "présence physique toujours inconnue" in window._reader_status_label.label
    toggle.callbacks["clicked"](toggle)
    assert db.device_binding_get(window._reader_selected_device_id)["enabled"] is False
    assert toggle.label == "Activer examens USB"
    assert db.device_placements() == before
    assert not errors


def test_legacy_binding_can_be_renamed_without_rebinding_or_disabling(tmp_path):
    db, window, errors = rig(tmp_path)
    # A registered source is enabled; a declarative rename must preserve it.
    db.device_usb_binding_declare(
        identity_id="alice", device_id="reader-alice",
        label="Before", usb_fingerprint="uuid-alice-0001", enabled=True,
    )
    dialog = open_dialog(window)
    dialog.name_field.value = "After"
    dialog.uuid_field.value = "uuid-alice-0001"
    dialog.respond("confirm")
    assert db.device_binding_get("reader-alice")["label"] == "After"
    assert db.device_binding_get("reader-alice")["enabled"] is True
    assert window._reader_selected_device_id == "reader-alice"
    assert not errors


@pytest.mark.parametrize("change", [
    "profile", "generation", "account_generation",
    "navigation", "closed", "superseded_dialog",
])
def test_stale_pairing_dialog_is_inert(tmp_path, change):
    db, window, errors = rig(tmp_path)
    dialog = open_dialog(window)
    dialog.name_field.value = "Private"
    dialog.uuid_field.value = "uuid-alice-9004"
    before = deepcopy(db.device_bindings())
    if change == "profile":
        window.service.current_identity = NS(identity_id="bob")
    elif change == "generation":
        window._identity_context_generation += 1
    elif change == "account_generation":
        window._account_context_generation += 1
    elif change == "navigation":
        window._route_display_token = object()
    elif change == "closed":
        window._closed = True
    else:
        open_dialog(window)
    dialog.respond("confirm")
    assert db.device_bindings() == before
    assert not errors


def test_duplicate_uuid_for_bob_is_refused_without_account_leak(tmp_path):
    db, window, errors = rig(tmp_path)
    dialog = open_dialog(window)
    dialog.name_field.value = "Tentative"
    dialog.uuid_field.value = "uuid-bob-0001"
    before = deepcopy(db.device_bindings())
    dialog.respond("confirm")
    assert db.device_bindings() == before
    assert errors == [(
        "Association impossible : vérifiez le profil, l'UUID et les "
        "liseuses déjà enregistrées.",
        "Liseuses",
    )]
    assert "uuid-bob-0001" not in repr(errors)


def test_no_profile_or_no_selected_reader_can_change_settings(tmp_path):
    db, window, errors = rig(tmp_path)
    before = deepcopy(db.device_bindings())
    window.service.current_identity = NS(identity_id="bob")
    request_reader_pairing(window, Gtk=GUI, Adw=ADW)
    assert db.device_bindings() == before
    toggle_reader_examination(window)
    assert db.device_bindings() == before
    assert not errors


def test_inert_pairing_action_exists_on_native_reader_page(tmp_path):
    db, window, errors = rig(tmp_path)
    controls = window.readers_page.header_actions.children
    assert len(controls) == 5
    assert [getattr(control, "_cifereca_action_id", None)
            for control in controls[1:]] == [
        "reader-usb-dry-run", "reader-usb-pair", "reader-usb-formats",
        "reader-usb-activation",
    ]
    assert window._reader_pair_button is controls[2]
    assert window._reader_formats_button is controls[3]
    assert window._reader_activation_button is controls[4]
    assert db.device_placements() == []
