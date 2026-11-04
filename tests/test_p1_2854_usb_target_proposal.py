"""P1 #2854: SHA-named target reservations are declarative, never USB copies."""
from __future__ import annotations

from copy import deepcopy

import pytest

from cifereca.core.device_usb_target_proposal import (
    UsbTargetProposal, propose_usb_target,
)
from tests.test_p1_2854_usb_target_path_intent import _db, _owned_book


SHA = "a" * 64


def inputs(fmt="epub"):
    artifact_id = "sha256:" + SHA
    binding = {
        "device_id": "reader-alice", "identity_id": "alice",
        "enabled": False, "target_directory": "Books/Cifereca",
        "metadata": {"usb_fingerprint": "uuid-alice-0001"},
    }
    intention = {
        "device_id": "reader-alice", "artifact_id": artifact_id,
        "desired_state": "keep", "observed_state": "unknown",
        "last_observed_at": None, "target_relative_path": None,
        "metadata": {"intent_identity_id": "alice"},
    }
    artifact = {
        "artifact_id": artifact_id, "sha256": SHA, "size": 42,
        "kind": "file", "media_format": fmt, "metadata": {},
    }
    return dict(
        identity_id="alice", binding=binding, intention=intention,
        artifact=artifact, selected_format=fmt,
        other_intentions=[intention],
    )


@pytest.mark.parametrize("fmt,suffix", [
    ("epub", ".epub"), ("kepub", ".kepub.epub"),
    ("fxl.kepub", ".fxl.kepub.epub"), ("pdf", ".pdf"),
])
def test_target_is_deterministic_exact_variant_and_non_executable(fmt, suffix):
    values = inputs(fmt)
    before = deepcopy(values)
    result = propose_usb_target(**values)
    assert result.target_relative_path == "Books/Cifereca/" + SHA + suffix
    assert result.identity_id == "alice"
    assert result.device_id == "reader-alice"
    assert result.artifact_id == "sha256:" + SHA
    assert result.usb_fingerprint == "uuid-alice-0001"
    assert result.selected_format == fmt
    assert result.reason == "destination_only_rights_unverified"
    assert result.executable is False
    assert values == before


@pytest.mark.parametrize("key,value", [
    ("media_format", "KEPUB"),
    ("media_format", "acsm"),
    ("media_format", None),
    ("sha256", "B" * 64),
    ("sha256", None),
    ("size", 0),
    ("size", True),
    ("kind", "bundle"),
    ("kind", None),
    ("artifact_id", "sha256:" + "b" * 64),
])
def test_invalid_artifact_or_format_is_refused(key, value):
    values = inputs()
    values["artifact"][key] = value
    with pytest.raises(ValueError):
        propose_usb_target(**values)


@pytest.mark.parametrize("key,value", [
    ("protected", True), ("artifact_protected", True),
    ("drm_status", "encrypted"), ("drm_status", "protected"),
    ("drm_scheme", "adept"), ("drm_scheme", "kdrm"), ("drm_scheme", "lcp"),
])
def test_observed_protection_is_never_reservable(key, value):
    values = inputs()
    values["artifact"]["metadata"][key] = value
    with pytest.raises(ValueError, match="protection_reported"):
        propose_usb_target(**values)


@pytest.mark.parametrize("metadata", [
    {}, {"protected": False}, {"artifact_protected": False},
    {"post_processed": True}, {"drm_scheme": "none"},
])
def test_inert_or_negative_protection_metadata_is_not_copy_permission(metadata):
    values = inputs()
    values["artifact"]["metadata"] = metadata
    proposal = propose_usb_target(**values)
    assert proposal.reason == "destination_only_rights_unverified"
    assert proposal.executable is False


@pytest.mark.parametrize("changes", [
    {"identity_id": "bob"}, {"device_id": "reader-bob"},
    {"target_directory": None}, {"target_directory": "../Books"},
    {"target_directory": "Books//Cifereca"},
    {"metadata": {}}, {"metadata": {"usb_fingerprint": "short"}},
    {"metadata": {"usb_fingerprint": "uuid bad !"}},
])
def test_malformed_binding_fails_closed(changes):
    values = inputs()
    values["binding"].update(changes)
    # A binding pointing to another reader is refused by the subsequent
    # intention/reader scope match, not necessarily by the first guard.
    with pytest.raises(ValueError, match="unverified"):
        propose_usb_target(**values)


@pytest.mark.parametrize("changes", [
    {"desired_state": "remove"},
    {"observed_state": "present"},
    {"last_observed_at": 1700000000},
    {"device_id": "reader-bob"},
    {"artifact_id": "sha256:" + "b" * 64},
    {"metadata": {}},
    {"metadata": {"intent_identity_id": "bob"}},
    {"metadata": {"intent_identity_id": "alice", "receipt": "unexpected"}},
])
def test_unqualified_intention_fails_closed(changes):
    values = inputs()
    values["intention"] = {**values["intention"], **changes}
    with pytest.raises(ValueError, match="intention_unverified"):
        propose_usb_target(**values)


def test_no_silent_upgrade_or_format_inference():
    values = inputs()
    values["selected_format"] = "kepub"
    with pytest.raises(ValueError, match="variant_unverified"):
        propose_usb_target(**values)


@pytest.mark.parametrize("path", [
    "Books/Cifereca/other.epub",
    "Books/Cifereca/" + SHA + ".kepub.epub",
    "/Books/Cifereca/" + SHA + ".epub",
])
def test_existing_target_path_is_immutable(path):
    values = inputs()
    values["intention"]["target_relative_path"] = path
    with pytest.raises(ValueError, match="immutable"):
        propose_usb_target(**values)


def test_duplicate_casefold_target_with_another_artifact_is_blocked():
    values = inputs()
    values["other_intentions"].append({
        "device_id": "reader-alice",
        "artifact_id": "some-other-artifact-id",
        "desired_state": "keep",
        "metadata": {"intent_identity_id": "alice"},
        "target_relative_path": "Books/Cifereca/" + SHA + ".EPUB",
    })
    with pytest.raises(ValueError, match="destination_occupied"):
        propose_usb_target(**values)


@pytest.mark.parametrize("sibling", [
    {"device_id": "reader-bob", "artifact_id": "foreign",
     "metadata": {"intent_identity_id": "alice"}},
    {"device_id": "reader-alice", "artifact_id": "foreign",
     "metadata": {"intent_identity_id": "bob"}},
    {"device_id": "reader-alice", "artifact_id": "foreign",
     "metadata": {"intent_identity_id": "alice"},
     "target_relative_path": "../escape"},
])
def test_ambiguous_or_foreign_device_inventory_refused(sibling):
    values = inputs()
    values["other_intentions"].append(sibling)
    with pytest.raises(ValueError, match="inventory_unverified"):
        propose_usb_target(**values)


def test_missing_current_intention_in_snapshot_refused():
    values = inputs()
    values["other_intentions"] = []
    with pytest.raises(ValueError, match="inventory_unverified"):
        propose_usb_target(**values)


def test_persisted_target_is_idempotent_and_no_usb_copy(tmp_path):
    db = _db(tmp_path)
    artifact_id, path = _owned_book(db, tmp_path)
    original_bytes = path.read_bytes()
    values = dict(
        identity_id="alice",
        binding=db.device_binding_get("usb-alice"),
        intention=db.device_placement_get("usb-alice", artifact_id),
        artifact=db.artifact_get(artifact_id),
        selected_format="epub",
        other_intentions=db.device_placements(device_id="usb-alice"),
    )
    proposal = propose_usb_target(**values)
    assert proposal.target_relative_path == (
        "Books/Cifereca/" + values["artifact"]["sha256"] + ".epub"
    )
    before = deepcopy(db.device_placement_get("usb-alice", artifact_id))
    db.device_usb_target_set(
        identity_id=proposal.identity_id,
        device_id=proposal.device_id,
        artifact_id=proposal.artifact_id,
        target_relative_path=proposal.target_relative_path,
    )
    after = db.device_placement_get("usb-alice", artifact_id)
    assert after["target_relative_path"] == proposal.target_relative_path
    for key in ("observed_state", "last_observed_at", "desired_state", "metadata"):
        assert after[key] == before[key]
    assert path.read_bytes() == original_bytes
    db.device_usb_target_set(
        identity_id=proposal.identity_id,
        device_id=proposal.device_id,
        artifact_id=proposal.artifact_id,
        target_relative_path=proposal.target_relative_path,
    )
    assert db.device_placement_get("usb-alice", artifact_id)["target_relative_path"] == proposal.target_relative_path
    # No physical USB or transfer receipt was created.
    assert db.device_placement_get("usb-alice", artifact_id)["observed_state"] == "unknown"


def test_other_account_cannot_reserve_target_for_shared_sha(tmp_path):
    db = _db(tmp_path)
    artifact_id, _ = _owned_book(db, tmp_path)
    values = dict(
        identity_id="bob",
        binding=db.device_binding_get("usb-bob"),
        intention=db.device_placement_get("usb-alice", artifact_id),
        artifact=db.artifact_get(artifact_id),
        selected_format="epub",
        other_intentions=db.device_placements(device_id="usb-alice"),
    )
    with pytest.raises(ValueError):
        propose_usb_target(**values)
    assert db.device_placement_get("usb-alice", artifact_id)["target_relative_path"] is None
