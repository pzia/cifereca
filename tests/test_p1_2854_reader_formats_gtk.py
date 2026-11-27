"""P1 #2854: explicit per-reader GTK format declarations never authorize USB I/O."""
from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace as NS

import pytest

from cifereca.gui.reader_format_actions import request_reader_formats
from tests.test_p1_2854_reader_pairing_gtk import Dialog, Group
from tests.test_p1_2854_readers_master_detail_page import environment


class Check:
    def __init__(self, *, label):
        self.label = label
        self.active = False

    def set_active(self, value):
        self.active = value

    def get_active(self):
        return self.active


class Dropdown:
    @classmethod
    def new_from_strings(cls, values):
        obj = cls()
        obj.values = values
        obj.selected = 0
        return obj

    def set_selected(self, index):
        self.selected = index

    def get_selected(self):
        return self.selected


GUI = NS(Orientation=NS(VERTICAL=1), Box=Group, CheckButton=Check,
         Label=lambda **kw: NS(**kw), DropDown=Dropdown)
ADW = NS(MessageDialog=Dialog)


def rig(tmp_path):
    db, entries, window, _ = environment(tmp_path)
    window._account_context_generation = 0
    window._route_display_token = object()
    window.errors = []
    window.show_error = lambda message, title: window.errors.append((message, title))
    window._render_readers()
    return db, window


def open_dialog(window):
    request_reader_formats(window, Gtk=GUI, Adw=ADW)
    dialog = getattr(window, "_reader_formats_dialog", None)
    assert dialog is not None and dialog.presented
    assert dialog.responses == {"cancel": "Annuler", "confirm": "Enregistrer"}
    assert "aucun" in dialog.description
    boxes = dialog.extra.children[:4]
    assert [box.label for box in boxes] == ["EPUB", "KEPUB", "FXL-KEPUB", "PDF"]
    assert isinstance(dialog.extra.children[-1], Dropdown)
    return dialog, boxes


def test_checkbox_declaration_persists_only_exact_reader_formats(tmp_path):
    db, window = rig(tmp_path)
    before = deepcopy(db.device_placements())
    initial = db.device_binding_get("reader-alice")
    assert window._reader_formats_button.sensitive is True
    assert "Formats liseuse non déclarés" in window._reader_status_label.label
    dialog, boxes = open_dialog(window)
    assert not any(box.get_active() for box in boxes)
    boxes[0].set_active(True)
    boxes[3].set_active(True)
    dialog.respond("confirm")
    changed = db.device_binding_get("reader-alice")
    assert changed["metadata"] == {
        **initial["metadata"], "reader_formats": ["epub", "pdf"],
    }
    assert changed["enabled"] == initial["enabled"]
    assert changed["target_directory"] == initial["target_directory"]
    assert db.device_placements() == before
    assert "Formats déclarés (non vérifiés) : EPUB, PDF" in (
        window._reader_status_label.label
    )
    assert not window.errors

    again, boxes = open_dialog(window)
    assert [item.get_active() for item in boxes] == [True, False, False, True]
    for box in boxes:
        box.set_active(False)
    again.respond("confirm")
    assert "reader_formats" not in db.device_binding_get("reader-alice")["metadata"]
    assert "Formats liseuse non déclarés" in window._reader_status_label.label


def test_cancel_does_not_change_storage_or_observation(tmp_path):
    db, window = rig(tmp_path)
    old = deepcopy(db.device_bindings())
    dialog, boxes = open_dialog(window)
    boxes[1].set_active(True)
    dialog.respond("cancel")
    assert db.device_bindings() == old
    assert window._reader_formats_dialog is None
    assert not window.errors


@pytest.mark.parametrize("stale", [
    "profile", "generation", "account", "route",
    "device", "selection", "closed", "superseded",
])
def test_stale_dialog_must_not_persist_anything(tmp_path, stale):
    db, window = rig(tmp_path)
    dialog, boxes = open_dialog(window)
    boxes[0].set_active(True)
    old = deepcopy(db.device_bindings())
    if stale == "profile":
        window.service.current_identity = NS(identity_id="bob")
    elif stale == "generation":
        window._identity_context_generation += 1
    elif stale == "account":
        window._account_context_generation += 1
    elif stale == "route":
        window._route_display_token = object()
    elif stale == "device":
        window._reader_selected_device_id = "reader-alice-2"
    elif stale == "selection":
        window._reader_selection_generation += 1
    elif stale == "closed":
        window._closed = True
    else:
        open_dialog(window)
    dialog.respond("confirm")
    assert db.device_bindings() == old
    assert not window.errors


def test_external_binding_update_cannot_be_lost_to_stale_dialog(tmp_path):
    db, window = rig(tmp_path)
    dialog, boxes = open_dialog(window)
    boxes[2].set_active(True)
    db.device_usb_reader_formats_set(
        identity_id="alice", device_id="reader-alice",
        usb_fingerprint="uuid-alice-0001", supported_formats=("pdf",),
    )
    dialog.respond("confirm")
    assert db.device_binding_get("reader-alice")["metadata"]["reader_formats"] == [
        "pdf",
    ]
    assert len(window.errors) == 1
    assert "Formats non enregistrés" in window.errors[0][0]


def test_changing_reader_does_not_copy_format_declaration(tmp_path):
    db, window = rig(tmp_path)
    dialog, boxes = open_dialog(window)
    boxes[0].set_active(True)
    dialog.respond("confirm")
    window._reader_device_dropdown.set_selected(1)
    window._reader_device_dropdown.trigger()
    assert window._reader_selected_device_id == "reader-alice-2"
    assert "Formats liseuse non déclarés" in window._reader_status_label.label
    second, controls = open_dialog(window)
    assert all(not control.get_active() for control in controls)
    controls[1].set_active(True)
    second.respond("confirm")
    assert db.device_binding_get("reader-alice")["metadata"]["reader_formats"] == [
        "epub",
    ]
    assert db.device_binding_get("reader-alice-2")["metadata"]["reader_formats"] == [
        "kepub",
    ]


def test_corrupt_or_foreign_binding_refuses_editor_without_mutation(tmp_path):
    db, window = rig(tmp_path)
    binding = db.device_binding_get("reader-alice")
    db.device_binding_put({
        **binding,
        "metadata": {**binding["metadata"], "reader_formats": ["not-a-format"]},
    })
    before = deepcopy(db.device_bindings())
    request_reader_formats(window, Gtk=GUI, Adw=ADW)
    assert getattr(window, "_reader_formats_dialog", None) is None
    assert db.device_bindings() == before
    assert window.errors


def test_editor_disabled_without_active_identity(tmp_path):
    db, window = rig(tmp_path)
    window.service.current_identity = None
    window._render_readers()
    assert window._reader_formats_button.sensitive is False
    old = deepcopy(db.device_bindings())
    request_reader_formats(window, Gtk=GUI, Adw=ADW)
    assert db.device_bindings() == old
    assert not window.errors


def test_preference_explicit_and_never_retargets_existing_wishes(tmp_path):
    db, window = rig(tmp_path)
    before = deepcopy(db.device_placements())
    dialog, boxes = open_dialog(window)
    boxes[0].set_active(True)
    boxes[3].set_active(True)
    dialog.extra.children[-1].set_selected(4)
    dialog.respond("confirm")
    metadata = db.device_binding_get("reader-alice")["metadata"]
    assert metadata["reader_formats"] == ["epub", "pdf"]
    assert metadata["reader_preferred_format"] == "pdf"
    assert db.device_placements() == before
    again, boxes = open_dialog(window)
    assert again.extra.children[-1].get_selected() == 4
    boxes[3].set_active(False)
    again.respond("confirm")
    assert db.device_binding_get("reader-alice")["metadata"] == metadata
    assert window.errors
    last, _ = open_dialog(window)
    last.extra.children[-1].set_selected(0)
    last.respond("confirm")
    assert "reader_preferred_format" not in db.device_binding_get("reader-alice")["metadata"]
    assert db.device_placements() == before
