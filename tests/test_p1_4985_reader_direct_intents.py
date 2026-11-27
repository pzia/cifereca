"""#4985: Liseuses offers explicit, scoped wishes, not USB execution."""
from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace as NS

import pytest

from cifereca.gui.reader_intent_actions import append_reader_intent_action
from tests.test_p1_2854_readers_master_detail_page import GTK, environment


def selected(tmp_path, *, wish=None):
    db, entries, window, _ = environment(tmp_path)
    book = entries[0]
    if wish is not None:
        # The storage contract rejects a first-time remove: a managed
        # keep intention must precede an explicit withdrawal wish.
        db.device_usb_intent_set(
            identity_id="alice", device_id="reader-alice",
            artifact_id=book["artifact_id"], desired_state="keep",
        )
        if wish == "remove":
            db.device_usb_intent_set(
                identity_id="alice", device_id="reader-alice",
                artifact_id=book["artifact_id"], desired_state="remove",
            )
    window._account_context_generation = 0
    window._route_display_token = object()
    window.errors = []
    window.show_error = lambda message, title: window.errors.append((message, title))
    window._render_readers()
    row = window.readers_page.rows[0]
    window.selected_row = row
    window.readers_page.selection = NS(get_selected_item=lambda: window.selected_row)
    actions = []
    window.readers_page.detail.add_actions = lambda buttons: actions.extend(buttons)
    window.readers_page.selected(row)
    return db, book, window, actions


class Dialog:
    def __init__(self):
        self.callbacks = {}
        self.responses = {}
        self.presented = False

    def add_response(self, key, label):
        self.responses[key] = label

    def set_close_response(self, key):
        self.close_response = key

    def connect(self, name, callback):
        self.callbacks[name] = callback

    def present(self):
        self.presented = True

    def answer(self, value):
        self.callbacks["response"](self, value)


def injected_button(window, *, dialog):
    actions = []
    book = window._reader_items[window.selected_row.key]
    assert append_reader_intent_action(
        window, NS(add_actions=lambda buttons: actions.extend(buttons)),
        book, Gtk=GTK, Adw=NS(MessageDialog=NS(new=lambda *args: dialog)),
    )
    assert len(actions) == 1
    return actions[0]


def test_none_to_keep_is_only_a_user_wish_and_repaints(tmp_path):
    db, book, window, actions = selected(tmp_path)
    assert [b._cifereca_action_id for b in actions] == ["reader-usb-intent:keep"]
    assert db.device_placements(device_id="reader-alice") == []
    old_file = db.placement_get(book["placement_id"])["path"]
    with open(old_file, "rb") as stream:
        raw = stream.read()
    actions[0].callbacks["clicked"](actions[0])
    intent = db.device_placement_get("reader-alice", book["artifact_id"])
    assert intent["desired_state"] == "keep"
    assert intent["observed_state"] == "unknown"
    assert intent["last_observed_at"] is None
    assert intent["target_relative_path"] == "Books/Cifereca/" + book["artifact_id"][7:] + ".epub"
    assert intent["metadata"] == {"intent_identity_id": "alice"}
    assert "Aucun fichier transféré ou supprimé" in window._reader_status_label.label
    assert window._reader_items[book["key"]].desired_state == "keep"
    assert not window.errors
    with open(old_file, "rb") as stream:
        assert stream.read() == raw


def test_keep_to_remove_needs_separate_confirmation_and_does_not_delete(tmp_path):
    db, book, window, _ = selected(tmp_path, wish="keep")
    dialog = Dialog()
    button = injected_button(window, dialog=dialog)
    assert button._cifereca_action_id == "reader-usb-intent:remove"
    before = deepcopy(db.device_placement_get("reader-alice", book["artifact_id"]))
    button.callbacks["clicked"](button)
    assert dialog.presented
    assert "cancel" in dialog.responses and "remove" in dialog.responses
    assert db.device_placement_get("reader-alice", book["artifact_id"]) == before
    dialog.answer("cancel")
    assert db.device_placement_get("reader-alice", book["artifact_id"]) == before
    button.callbacks["clicked"](button)
    dialog.answer("remove")
    after = db.device_placement_get("reader-alice", book["artifact_id"])
    assert after["desired_state"] == "remove"
    for key in ("observed_state", "last_observed_at", "target_relative_path", "metadata"):
        assert after[key] == before[key]
    assert not window.errors


def test_existing_remove_to_keep_is_explicit_and_preserves_observation(tmp_path):
    db, book, window, actions = selected(tmp_path, wish="remove")
    assert [b._cifereca_action_id for b in actions] == ["reader-usb-intent:keep"]
    before = deepcopy(db.device_placement_get("reader-alice", book["artifact_id"]))
    actions[0].callbacks["clicked"](actions[0])
    after = db.device_placement_get("reader-alice", book["artifact_id"])
    assert after["desired_state"] == "keep"
    assert after["metadata"] == before["metadata"]
    assert after["observed_state"] == before["observed_state"]
    assert after["last_observed_at"] == before["last_observed_at"]
    assert not window.errors


@pytest.mark.parametrize("change", [
    "profile", "device", "generation", "account", "route", "selection",
    "snapshot", "closed", "placement", "newer_intent",
])
def test_stale_click_cannot_rewrite_other_intentions(tmp_path, change):
    db, book, window, actions = selected(tmp_path)
    button = actions[0]
    if change == "profile":
        window.service.current_identity.identity_id = "bob"
    elif change == "device":
        window._reader_selected_device_id = "reader-alice-2"
    elif change == "generation":
        window._identity_context_generation += 1
    elif change == "account":
        window._account_context_generation += 1
    elif change == "route":
        window._route_display_token = object()
    elif change == "selection":
        window.selected_row = NS(key="other")
    elif change == "snapshot":
        window._reader_library_snapshot = object()
    elif change == "closed":
        window._closed = True
    elif change == "placement":
        placement = db.placement_get(book["placement_id"])
        placement["state"] = "missing"
        db.placement_put(placement)
    else:
        db.device_usb_intent_set(
            identity_id="alice", device_id="reader-alice",
            artifact_id=book["artifact_id"], desired_state="keep",
        )
    before = deepcopy(db.device_placements(device_id="reader-alice"))
    button.callbacks["clicked"](button)
    assert db.device_placements(device_id="reader-alice") == before
    # A live but stale SQL form gets a closed refusal, never a silent write.
    assert window.errors == [] or change in ("placement", "newer_intent")


@pytest.mark.parametrize("change", [
    "profile", "device", "generation", "account", "route",
    "selection", "snapshot", "closed",
])
def test_stale_removal_dialog_cannot_change_sqlite(tmp_path, change):
    db, book, window, _ = selected(tmp_path, wish="keep")
    dialog = Dialog()
    button = injected_button(window, dialog=dialog)
    button.callbacks["clicked"](button)
    assert dialog.presented
    before = deepcopy(db.device_placement_get("reader-alice", book["artifact_id"]))
    if change == "profile":
        window.service.current_identity.identity_id = "bob"
    elif change == "device":
        window._reader_selected_device_id = "reader-alice-2"
    elif change == "generation":
        window._identity_context_generation += 1
    elif change == "account":
        window._account_context_generation += 1
    elif change == "route":
        window._route_display_token = object()
    elif change == "selection":
        window.selected_row = NS(key="other")
    elif change == "snapshot":
        window._reader_library_snapshot = object()
    else:
        window._closed = True
    dialog.answer("remove")
    assert db.device_placement_get("reader-alice", book["artifact_id"]) == before
    assert not window.errors


def test_foreign_intention_never_offers_direct_action(tmp_path):
    db, book, window, _ = selected(tmp_path)
    db.device_placement_put({
        "device_id": "reader-alice", "artifact_id": book["artifact_id"],
        "desired_state": "keep", "observed_state": "unknown",
        "metadata": {"intent_identity_id": "bob"},
    })
    actions = []
    assert not append_reader_intent_action(
        window, NS(add_actions=lambda buttons: actions.extend(buttons)),
        window._reader_items[book["key"]], Gtk=GTK,
    )
    assert actions == []
