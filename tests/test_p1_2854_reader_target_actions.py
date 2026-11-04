"""P1 #2854: Liseuses detail pins a target, never executes USB I/O."""
from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace as NS

import pytest

from cifereca.gui import reader_page_controller
from tests.test_p1_2854_readers_master_detail_page import environment


def prepare(tmp_path):
    db, entries, host, local_actions = environment(tmp_path)
    book = entries[0]
    db.device_usb_intent_set(
        identity_id="alice", device_id="reader-alice",
        artifact_id=book["artifact_id"], desired_state="keep",
    )
    host._identity_context_generation = 0
    host._account_context_generation = 0
    host._route_display_token = object()
    host.show_error = lambda message, title: host.errors.append((message, title))
    host.errors = []
    host._render_readers()
    row = host.readers_page.rows[0]
    actions = []
    host.readers_page.detail.add_actions = lambda buttons: actions.extend(buttons)
    host.readers_page.selection = NS(get_selected_item=lambda: host.selected_row)
    host.selected_row = row
    host.readers_page.selected(row)
    assert len(actions) == 1
    assert actions[0]._cifereca_action_id == "reader-usb-target-reserve"
    return db, book, host, actions[0], local_actions


def test_user_click_pins_sha_path_and_changes_no_usb_or_local_bytes(tmp_path):
    db, book, host, button, local_actions = prepare(tmp_path)
    before = db.device_placement_get("reader-alice", book["artifact_id"])
    owned = db.placement_get(book["placement_id"])
    raw = open(owned["path"], "rb").read()
    artifact = db.artifact_get(book["artifact_id"])
    assert button.label == "Réserver emplacement EPUB"
    assert "Aucun transfert" in button.label or button._cifereca_action_id
    button.callbacks["clicked"](button)
    after = db.device_placement_get("reader-alice", book["artifact_id"])
    assert after["target_relative_path"] == (
        "Books/Cifereca/" + artifact["sha256"] + ".epub"
    )
    for key in ("desired_state", "observed_state", "last_observed_at", "metadata"):
        assert after[key] == before[key]
    with open(owned["path"], "rb") as handle:
        assert handle.read() == raw
    assert after["observed_state"] == "unknown"
    assert host.errors == []
    assert "aucun fichier transféré" in host._reader_status_label.label
    assert local_actions[-1]["identity_id"] == "alice"


@pytest.mark.parametrize("change", [
    "profile", "identity_generation", "account_generation",
    "route", "device", "selected_book", "snapshot", "closed",
])
def test_stale_detail_button_cannot_reserve_target(tmp_path, change):
    db, book, host, button, _ = prepare(tmp_path)
    before = deepcopy(db.device_placement_get("reader-alice", book["artifact_id"]))
    if change == "profile":
        host.service.current_identity.identity_id = "bob"
    elif change == "identity_generation":
        host._identity_context_generation += 1
    elif change == "account_generation":
        host._account_context_generation += 1
    elif change == "route":
        host._route_display_token = object()
    elif change == "device":
        host._reader_selected_device_id = "reader-alice-2"
    elif change == "selected_book":
        host.selected_row = NS(key="another-book")
    elif change == "snapshot":
        host._reader_library_snapshot = object()
    else:
        host._closed = True
    button.callbacks["clicked"](button)
    assert db.device_placement_get("reader-alice", book["artifact_id"]) == before
    assert not host.errors


@pytest.mark.parametrize("change", [
    "remove_wish", "foreign_wish", "local_missing",
    "format_changed", "protected", "already_reserved",
])
def test_changed_sqlite_evidence_refuses_stale_reservation(tmp_path, change):
    db, book, host, button, _ = prepare(tmp_path)
    if change == "remove_wish":
        db.device_usb_intent_set(
            identity_id="alice", device_id="reader-alice",
            artifact_id=book["artifact_id"], desired_state="remove",
        )
    elif change == "foreign_wish":
        intent = db.device_placement_get("reader-alice", book["artifact_id"])
        intent["metadata"] = {"intent_identity_id": "bob"}
        db.device_placement_put(intent)
    elif change == "local_missing":
        placement = db.placement_get(book["placement_id"])
        placement["state"] = "missing"
        db.placement_put(placement)
    elif change in {"format_changed", "protected"}:
        artifact = db.artifact_get(book["artifact_id"])
        if change == "format_changed":
            artifact["media_format"] = "pdf"
        else:
            artifact["metadata"]["artifact_protected"] = True
        db.artifact_put(artifact)
    else:
        db.device_usb_target_set(
            identity_id="alice", device_id="reader-alice",
            artifact_id=book["artifact_id"],
            target_relative_path="Books/Cifereca/manual.epub",
        )
    before = deepcopy(db.device_placement_get("reader-alice", book["artifact_id"]))
    button.callbacks["clicked"](button)
    assert db.device_placement_get("reader-alice", book["artifact_id"]) == before
    assert host.errors
    assert "Destination non réservée" in host.errors[0][0]


def test_no_intention_no_action_and_no_implicit_reservation(tmp_path):
    db, entries, host, _ = environment(tmp_path)
    host._render_readers()
    row = host.readers_page.rows[0]
    actions = []
    host.readers_page.detail.add_actions = lambda buttons: actions.extend(buttons)
    host.readers_page.selected(row)
    assert actions == []
    assert db.device_placements(device_id="reader-alice") == []


def test_second_visit_after_reservation_does_not_offer_retarget(tmp_path):
    db, book, host, button, _ = prepare(tmp_path)
    button.callbacks["clicked"](button)
    actions = []
    host.readers_page.detail.add_actions = lambda buttons: actions.extend(buttons)
    host.selected_row = host.readers_page.rows[0]
    host.readers_page.selected(host.selected_row)
    assert actions == []


def test_target_action_does_not_replace_existing_library_actions(tmp_path):
    db, book, host, button, local_actions = prepare(tmp_path)
    assert local_actions
    assert local_actions[-1]["artifact_id"] == book["artifact_id"]
    assert button._cifereca_action_id == "reader-usb-target-reserve"
