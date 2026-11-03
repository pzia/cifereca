"""P1 #2854: one USB device belongs to one selected identity.

Binding is *declarative*: no mount discovery or USB read/write occurs.
The actual volume fingerprint must be attested by a later USB observer.
"""
from __future__ import annotations

from copy import deepcopy

import pytest

from cifereca.identities import Identity
from cifereca.storage import Storage


def _db(tmp_path):
    storage = Storage(tmp_path / "library.sqlite3")
    storage.identity_put(Identity("alice", "Alice"))
    storage.identity_put(Identity("bob", "Bob"))
    return storage


def _register(db, *, owner="alice", device="reader-a", fingerprint="uuid-001-alpha",
              label="Ma liseuse", enabled=False):
    return db.device_usb_binding_declare(
        identity_id=owner, device_id=device, label=label,
        usb_fingerprint=fingerprint, enabled=enabled,
    )


def test_new_usb_binding_is_inert_but_identity_scoped(tmp_path):
    db = _db(tmp_path)
    assert _register(db) == "reader-a"
    row = db.device_binding_get("reader-a")
    assert row["identity_id"] == "alice"
    assert row["label"] == "Ma liseuse"
    assert row["enabled"] is False
    assert row["target_directory"] == "Books/Cifereca"
    assert row["metadata"] == {"usb_fingerprint": "uuid-001-alpha"}
    assert db.device_bindings(identity_id="bob") == []
    assert not db.device_placements()
    assert db.device_bindings(identity_id="alice") == [row]


def test_updating_same_device_never_rebinds_or_resets_observed_state(tmp_path):
    db = _db(tmp_path)
    _register(db)
    initial = db.device_binding_get("reader-a")
    # Existing backend metadata is opaque, not a form to rebuild.
    db.device_binding_put({
        **initial, "target_directory": "Books/Managed",
        "metadata": {**initial["metadata"], "vendor_extension": ["retained"]},
    })
    db.device_placement_put({
        "device_id": "reader-a", "artifact_id": "owned-artifact",
        "desired_state": "remove", "observed_state": "present",
        "last_observed_at": 1720000000, "target_relative_path": "Books/Managed/a.epub",
        "metadata": {"intent_identity_id": "alice", "receipt": "stable"},
    })
    before = deepcopy(db.device_placements())
    _register(db, label="Nouvelle étiquette", enabled=True)
    row = db.device_binding_get("reader-a")
    assert row["label"] == "Nouvelle étiquette"
    assert row["enabled"] is True
    assert row["identity_id"] == "alice"
    assert row["target_directory"] == "Books/Managed"
    assert row["metadata"]["vendor_extension"] == ["retained"]
    assert db.device_placements() == before
    # Explicitly disabling the same reader retains the same database records.
    _register(db, label="Nouvelle étiquette", enabled=False)
    assert db.device_binding_get("reader-a")["enabled"] is False
    assert db.device_placements() == before


def test_same_hardware_cannot_be_aliased_to_another_profile_or_device(tmp_path):
    db = _db(tmp_path)
    _register(db)
    before = deepcopy(db.device_bindings())
    for owner, device, fingerprint in (
        ("bob", "reader-b", "UUID-001-ALPHA"),
        ("alice", "reader-c", "uuid-001-alpha"),
        ("bob", "reader-a", "uuid-001-alpha"),
        ("alice", "reader-a", "uuid-another"),
    ):
        with pytest.raises(ValueError, match="already bound|rebind"):
            _register(db, owner=owner, device=device, fingerprint=fingerprint)
        assert db.device_bindings() == before


def test_legacy_binding_without_usb_fingerprint_requires_explicit_migration(tmp_path):
    db = _db(tmp_path)
    db.device_binding_put({
        "device_id": "legacy", "identity_id": "alice",
        "label": "Historique", "metadata": {"opaque": "keep"},
    })
    before = deepcopy(db.device_bindings())
    with pytest.raises(ValueError, match="rebind"):
        _register(db, device="legacy", fingerprint="uuid-legacy-001")
    assert db.device_bindings() == before
    assert db.device_binding_get("legacy")["metadata"] == {"opaque": "keep"}


def test_two_profiles_can_bind_multiple_distinct_readers(tmp_path):
    db = _db(tmp_path)
    for owner, device, fingerprint in (
        ("alice", "reader-a", "uuid-001-alpha"),
        ("alice", "reader-b", "uuid-002-beta"),
        ("bob", "reader-c", "uuid-003-gamma"),
    ):
        _register(db, owner=owner, device=device, fingerprint=fingerprint)
    assert {row["device_id"] for row in db.device_bindings(identity_id="alice")} == {
        "reader-a", "reader-b",
    }
    assert {row["device_id"] for row in db.device_bindings(identity_id="bob")} == {
        "reader-c",
    }


@pytest.mark.parametrize("changed", [
    {"enabled": None}, {"enabled": 1}, {"enabled": "true"},
    {"label": ""}, {"label": " 	 "}, {"label": 123},
    {"fingerprint": ""}, {"fingerprint": "../../etc/passwd"},
    {"fingerprint": "short"}, {"fingerprint": 1},
    {"device": ""}, {"device": None},
    {"owner": "unconfigured"}, {"owner": None},
])
def test_malformed_unowned_or_unverified_binding_is_not_created(tmp_path, changed):
    db = _db(tmp_path)
    before = deepcopy(db.device_bindings())
    with pytest.raises(ValueError):
        _register(db, **changed)
    assert db.device_bindings() == before


def test_usb_binding_survives_restart_without_claiming_mount_presence(tmp_path):
    db = _db(tmp_path)
    _register(db, enabled=True)
    restarted = Storage(tmp_path / "library.sqlite3")
    item = restarted.device_binding_get("reader-a")
    assert item["identity_id"] == "alice"
    assert item["metadata"]["usb_fingerprint"] == "uuid-001-alpha"
    assert item["enabled"] is True
    # The binding does not prove anything about a mounted device or its books.
    assert restarted.device_placements() == []
