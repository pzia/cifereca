"""P1 #2854: USB binding proposal is deterministic, scoped and inert."""
from __future__ import annotations

from copy import deepcopy

import pytest

from cifereca.core.device_usb_binding_proposal import (
    UsbBindingProposal, propose_usb_reader_binding,
)
from cifereca.identities import Identity
from cifereca.storage import Storage


FINGERPRINT = "uuid-alice-0001"


def source(tmp_path):
    storage = Storage(tmp_path / "library.sqlite3")
    for identity in ("alice", "bob"):
        storage.identity_put(Identity(identity, identity.capitalize()))
    return storage


def propose(storage, *, identity="alice", label="Ma liseuse",
            fingerprint=FINGERPRINT):
    return propose_usb_reader_binding(
        identity_id=identity, label=label, usb_fingerprint=fingerprint,
        existing=storage.device_bindings(),
    )


def test_new_binding_is_stable_inert_and_not_saved_without_confirmation(tmp_path):
    db = source(tmp_path)
    before = deepcopy(db.device_bindings())
    first = propose(db)
    second = propose(db)
    assert first == second
    assert type(first) is UsbBindingProposal
    assert first.identity_id == "alice"
    assert first.device_id.startswith("usb-")
    assert len(first.device_id) == 36
    assert "alice" not in first.device_id
    assert "uuid" not in first.device_id
    assert first.usb_fingerprint == FINGERPRINT
    assert first.label == "Ma liseuse"
    assert first.already_bound is False
    assert first.enabled is False
    assert first.physically_attested is False
    assert first.executable is False
    assert db.device_bindings() == before
    assert db.device_placements() == []


def test_storage_must_be_called_separately_after_an_explicit_choice(tmp_path):
    db = source(tmp_path)
    proposal = propose(db)
    assert db.device_binding_get(proposal.device_id) is None
    db.device_usb_binding_declare(
        identity_id=proposal.identity_id, device_id=proposal.device_id,
        label=proposal.label, usb_fingerprint=proposal.usb_fingerprint,
        enabled=False,
    )
    existing = propose(db, label="Une liseuse existante")
    assert existing.already_bound is True
    assert existing.device_id == proposal.device_id
    assert existing.enabled is False
    assert db.device_binding_get(proposal.device_id)["label"] == "Ma liseuse"
    assert db.device_binding_get(proposal.device_id)["enabled"] is False


def test_different_profile_must_not_inherit_same_volume_uuid(tmp_path):
    db = source(tmp_path)
    db.device_usb_binding_declare(
        identity_id="alice", device_id="old-usb",
        label="Alice", usb_fingerprint=FINGERPRINT,
    )
    before = deepcopy(db.device_bindings())
    with pytest.raises(ValueError, match="bound or ambiguous"):
        propose(db, identity="bob")
    assert db.device_bindings() == before


def test_two_profiles_create_independent_hashed_ids_for_distinct_volumes(tmp_path):
    db = source(tmp_path)
    a = propose(db)
    b = propose(db, identity="bob", fingerprint="uuid-bob-0002")
    assert a.device_id != b.device_id
    assert b.identity_id == "bob"
    assert b.executable is False


def test_same_casefold_uuid_with_different_case_is_not_silently_renamed(tmp_path):
    db = source(tmp_path)
    db.device_usb_binding_declare(
        identity_id="alice", device_id="previous",
        label="Reader", usb_fingerprint="UUID-ALICE-0001",
    )
    with pytest.raises(ValueError, match="case changed"):
        propose(db)


def test_existing_nonstandard_device_id_is_reused_not_replaced(tmp_path):
    db = source(tmp_path)
    db.device_usb_binding_declare(
        identity_id="alice", device_id="legacy-reader",
        label="Ancienne", usb_fingerprint=FINGERPRINT,
        enabled=True,
    )
    proposal = propose(db)
    assert proposal.device_id == "legacy-reader"
    assert proposal.already_bound is True
    assert proposal.enabled is True  # Existing configuration is preserved.
    assert proposal.physically_attested is False
    assert db.device_binding_get("legacy-reader")["enabled"] is True


def test_legacy_non_usb_binding_does_not_grant_fingerprint(tmp_path):
    db = source(tmp_path)
    db.device_binding_put({
        "device_id": "legacy-cloud", "identity_id": "alice",
        "label": "Cloud", "metadata": {"kind": "cloud"},
    })
    proposal = propose(db)
    assert proposal.already_bound is False
    assert proposal.device_id != "legacy-cloud"


def test_a_colliding_generated_device_id_must_not_rebind_existing_owner(tmp_path):
    db = source(tmp_path)
    generated = propose(db)
    db.device_binding_put({
        "device_id": generated.device_id,
        "identity_id": "bob",
        "metadata": {"usb_fingerprint": "uuid-bob-0002"},
    })
    with pytest.raises(ValueError, match="ID collision"):
        propose(db)
    assert db.device_binding_get(generated.device_id)["identity_id"] == "bob"


def test_profile_maximum_is_guarded_before_creation(tmp_path):
    db = source(tmp_path)
    for index in range(16):
        db.device_usb_binding_declare(
            identity_id="alice", device_id=f"reader-{index}",
            label=f"Liseuse {index}",
            usb_fingerprint=f"uuid-alice-{index:04d}",
        )
    with pytest.raises(ValueError, match="reader limit"):
        propose(db, fingerprint="uuid-alice-9999")
    assert len(db.device_bindings(identity_id="alice")) == 16


@pytest.mark.parametrize("fingerprint", [
    "", "test", "/dev/sdb1", "../path", "uuid Alice 1",
    "uuid-\n-0001", "uuid-☃-0001", "u" * 129, None, True,
])
def test_malformed_uuid_is_refused_without_mutating_bindings(tmp_path, fingerprint):
    db = source(tmp_path)
    before = deepcopy(db.device_bindings())
    with pytest.raises(ValueError, match="invalid USB"):
        propose(db, fingerprint=fingerprint)
    assert db.device_bindings() == before


@pytest.mark.parametrize("identity,label", [
    ("", "Liseuse"), (" alice", "Liseuse"), ("alice ", "Liseuse"),
    ("alice", ""), ("alice", " Liseuse"), ("alice", "Liseuse\nX"),
    ("alice", "X" * 91), (None, "Liseuse"),
])
def test_invalid_profile_or_label_never_creates_proposal(
    tmp_path, identity, label,
):
    db = source(tmp_path)
    before = deepcopy(db.device_bindings())
    with pytest.raises(ValueError):
        propose(db, identity=identity, label=label)
    assert db.device_bindings() == before
