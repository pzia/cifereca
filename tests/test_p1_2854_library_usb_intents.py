"""P1 #2854: Library reader actions are declarative and identity-scoped.

Actual Storage and existing headless GTK test facades; no USB, library
source materialization, deletion, provider acquisition or external rights.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cifereca.identities import Identity
from cifereca.storage import Storage
from cifereca.gui.local_book_action_controller import add_local_book_actions
from tests.test_core_ui_1129_local_book_actions import _environment


BOOK = b"synthetic epub file for USB intent UI"
SHA = sha256(BOOK).hexdigest()
ARTIFACT = "sha256:" + SHA


def setup(tmp_path, *, devices=1, with_local=True):
    db = Storage(tmp_path / "library.sqlite3")
    db.identity_put(Identity("alice", "Alice"))
    db.identity_put(Identity("bob", "Bob"))
    saved = tmp_path / "alice" / "book.epub"
    saved.parent.mkdir()
    saved.write_bytes(BOOK)
    db.artifact_put({
        "artifact_id": ARTIFACT, "sha256": SHA,
        "size": len(BOOK), "media_format": "epub",
        "metadata": {"artifact_kind": "file"},
    })
    placement_id = db.placement_put({
        "artifact_id": ARTIFACT, "path": str(saved), "identity_id": "alice",
        "provider_id": "local", "source_item_id": "book-1",
        "state": "present", "metadata": {},
    })
    for index in range(devices):
        db.device_usb_binding_declare(
            identity_id="alice", device_id=f"reader-{index}",
            usb_fingerprint=f"uuid-alice-{index:04d}",
            label=f"Liseuse {index}", enabled=index % 2 == 0,
        )
    item = {
        "key": "book", "identity_id": "alice", "artifact_id": ARTIFACT,
        "placement_id": placement_id, "local": True,
        "provider_id": "local", "title": "Local EPUB",
    }
    window, _, detail, actions, events, submitted, launched, dependencies = (
        _environment(item=item, identities=("alice",), local=with_local)
    )
    groups = []
    detail.add_group = lambda title, rows: groups.append((title, rows))
    window.service.storage = db
    window._identity_context_generation = 10
    return (db, saved, placement_id, window, item, detail,
            actions, events, submitted, groups, dependencies)


def render(setup_result):
    _, _, _, window, item, detail, actions, _, _, _, deps = setup_result
    add_local_book_actions(window, detail, item, **deps)
    menu = next((a for a in actions
                 if getattr(a, "_cifereca_action_id", None) == "usb-intents"), None)
    return menu


def test_new_intent_menu_is_passive_and_never_triggers_usb_transfer(tmp_path):
    ctx = setup(tmp_path)
    db, path, placement, window, item, detail, actions, events, jobs, groups, _ = ctx
    baseline = path.read_bytes()
    menu = render(ctx)
    assert menu is not None
    assert [a._cifereca_action_id for a in actions] == [
        "open-local", "usb-intents",
    ]
    assert len(menu.popover.child.children) == 1
    button, = menu.popover.child.children
    assert button.label == "Mettre sur Liseuse 0"
    assert groups == [
        ("Liseuses · intentions", [
            ("Liseuse 0", "Aucun souhait · Présence actuelle non confirmée")
        ]),
    ]
    assert db.device_placements() == []
    assert not jobs
    button.click()
    saved = db.device_placement_get("reader-0", ARTIFACT)
    assert saved["desired_state"] == "keep"
    assert saved["observed_state"] == "unknown"
    assert saved["target_relative_path"] is None
    assert saved["last_observed_at"] is None
    assert saved["metadata"] == {"intent_identity_id": "alice"}
    assert path.read_bytes() == baseline
    assert not jobs
    assert any("Aucun fichier" in event[1] for event in events
               if event[0] == "activity")


def test_remove_is_shown_only_for_prior_managed_intent(tmp_path):
    ctx = setup(tmp_path)
    db, path, _, window, _, detail, actions, events, jobs, groups, deps = ctx
    first = render(ctx)
    assert first.popover.child.children[0].label.startswith("Mettre")
    first.popover.child.children[0].click()
    previous = db.device_placement_get("reader-0", ARTIFACT)
    # Old popover can become stale; new detail computes the next step.
    actions.clear()
    groups.clear()
    second = render(ctx)
    assert second.popover.child.children[0].label == "Retirer de Liseuse 0"
    second.popover.child.children[0].click()
    changed = db.device_placement_get("reader-0", ARTIFACT)
    assert changed["desired_state"] == "remove"
    for key in ("metadata", "target_relative_path", "observed_state",
                "last_observed_at", "created_at", "device_placement_id"):
        assert changed[key] == previous[key]
    assert path.read_bytes() == BOOK
    assert not jobs


def test_two_readers_are_separately_scoped_and_activation_does_not_copy(tmp_path):
    ctx = setup(tmp_path, devices=2)
    db, _, _, window, _, detail, actions, events, jobs, groups, _ = ctx
    menu = render(ctx)
    assert [a.label for a in menu.popover.child.children] == [
        "Mettre sur Liseuse 0", "Mettre sur Liseuse 1",
    ]
    assert "Appareil désactivé" in groups[0][1][1][1]
    menu.popover.child.children[1].click()
    assert db.device_placement_get("reader-1", ARTIFACT)["desired_state"] == "keep"
    assert db.device_placement_get("reader-0", ARTIFACT) is None
    assert db.device_binding_get("reader-1")["enabled"] is False
    assert not jobs


@pytest.mark.parametrize("change", [
    "current_identity", "roundtrip_generation", "closed",
    "detached", "hidden", "local_missing", "placement_reassigned",
    "device_reassigned", "desired_changed",
])
def test_stale_context_cannot_mutate_intents(tmp_path, change):
    ctx = setup(tmp_path)
    db, saved, placement_id, window, item, detail, actions, events, jobs, _, _ = ctx
    db.device_usb_intent_set(
        identity_id="alice", device_id="reader-0",
        artifact_id=ARTIFACT, desired_state="keep",
    )
    menu = render(ctx)
    button = menu.popover.child.children[0]
    assert button.label.startswith("Retirer")
    before = deepcopy(db.device_placements())
    if change == "current_identity":
        window.service.current_identity = NS(identity_id="bob")
    elif change == "roundtrip_generation":
        window._identity_context_generation += 2
    elif change == "closed":
        window._closed = True
    elif change == "detached":
        button.get_root = lambda: None
    elif change == "hidden":
        button.get_mapped = lambda: False
    elif change == "local_missing":
        window.service.exists = False
    elif change == "placement_reassigned":
        window.service.storage.placement_get = lambda _id: {
            "placement_id": placement_id, "artifact_id": ARTIFACT,
            "identity_id": "bob", "state": "present",
        }
    elif change == "device_reassigned":
        db.device_binding_put({
            "device_id": "reader-0", "identity_id": "bob",
            "metadata": {"usb_fingerprint": "uuid-bob-9000"},
        })
    else:
        db.device_usb_intent_set(
            identity_id="alice", device_id="reader-0",
            artifact_id=ARTIFACT, desired_state="remove",
        )
        before = deepcopy(db.device_placements())
    button.click()
    assert db.device_placements() == before
    assert not jobs


def test_malformed_or_foreign_books_have_no_reader_controls(tmp_path):
    ctx = setup(tmp_path)
    db, _, placement_id, window, item, detail, actions, events, jobs, _, deps = ctx
    item["identity_id"] = "bob"
    assert render(ctx) is None
    assert not actions or all(getattr(a, "_cifereca_action_id", "") != "usb-intents"
                              for a in actions)
    item["identity_id"] = "alice"
    item["placement_id"] = "nonexistent"
    actions.clear()
    assert render(ctx) is None
    assert db.device_placements() == []


def test_legacy_unowned_intent_disables_menu_without_exposing_old_profile(tmp_path):
    ctx = setup(tmp_path)
    db, _, _, window, _, _, actions, events, jobs, groups, _ = ctx
    db.device_placement_put({
        "device_id": "reader-0", "artifact_id": ARTIFACT,
        "desired_state": "keep", "observed_state": "present",
        "target_relative_path": "Books/Cifereca/old.epub",
        "metadata": {"intent_identity_id": "bob", "token": "SECRET"},
    })
    assert render(ctx) is None
    assert groups == []
    assert "SECRET" not in repr(actions) + repr(events)
    assert not jobs


def test_missing_storage_or_profile_read_does_not_break_existing_actions(tmp_path):
    ctx = setup(tmp_path)
    db, _, _, window, _, _, actions, events, jobs, groups, _ = ctx
    del window.service.storage
    assert render(ctx) is None
    assert [a._cifereca_action_id for a in actions] == ["open-local"]
    assert not jobs


def test_legacy_non_usb_device_binding_does_not_get_a_reader_action(tmp_path):
    ctx = setup(tmp_path, devices=0)
    db, _, _, window, _, _, actions, events, jobs, groups, _ = ctx
    db.device_binding_put({
        "device_id": "non-usb-legacy", "identity_id": "alice",
        "label": "Stockage historique",
        "enabled": True, "metadata": {"plugin_kind": "cloud"},
    })
    assert render(ctx) is None
    assert [a._cifereca_action_id for a in actions] == ["open-local"]
    assert groups == [] and db.device_placements() == [] and not jobs


def test_rebound_usb_fingerprint_after_render_makes_old_click_inert(tmp_path):
    ctx = setup(tmp_path)
    db, _, _, window, _, _, actions, events, jobs, groups, _ = ctx
    menu = render(ctx)
    button, = menu.popover.child.children
    old = db.device_binding_get("reader-0")
    db.device_binding_put({
        **old, "metadata": {"usb_fingerprint": "uuid-new-swap-9090"},
    })
    before = deepcopy(db.device_placements())
    button.click()
    assert db.device_placements() == before
    assert not jobs
    assert ("error", "Liseuses") in events
