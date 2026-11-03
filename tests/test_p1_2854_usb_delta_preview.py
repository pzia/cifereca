"""P1 #2854: no USB write or removal from declarative device preview."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

import pytest

from cifereca.core.device_usb_preview import (
    UsbDeviceScan, UsbLocalArtifact, UsbManagedReceipt,
    UsbObservedFile, preview_usb_delta,
)
from cifereca.storage import Storage


SHA_A, SHA_B = "a" * 64, "b" * 64
NOW = 1800000000
FINGERPRINT = "usb-explicit-uuid-A"
FILE_PATH = "Books/Paris/a.epub"


def binding(*, device="reader-a", identity="alice", enabled=True, metadata=None):
    return {
        "device_id": device, "identity_id": identity, "enabled": enabled,
        "metadata": {"usb_fingerprint": FINGERPRINT} if metadata is None else metadata,
    }


def intent(*, device="reader-a", artifact="artifact-a", desired="keep",
           observed="present", observed_at=NOW - 600, owner="alice"):
    return {
        "device_id": device, "artifact_id": artifact,
        "desired_state": desired, "observed_state": observed,
        "last_observed_at": observed_at,
        "target_relative_path": FILE_PATH,
        "metadata": {"intent_identity_id": owner},
    }


def local(*, owner="alice", digest=SHA_A, verified=True):
    return {"artifact-a": UsbLocalArtifact(owner, digest, verified)}


def scan(*, device="reader-a", identity="alice", fingerprint=FINGERPRINT,
         when=NOW - 20, complete=True, files=None):
    return UsbDeviceScan(
        identity_id=identity, device_id=device, fingerprint=fingerprint,
        observed_at=when, complete=complete,
        files={} if files is None else files,
    )


def preview(*, placements=None, scan_value=None, receipts=None,
            local_value=None, identity="alice", target=None):
    return preview_usb_delta(
        identity_id=identity, binding=target or binding(),
        placements=placements if placements is not None else [intent()],
        local=local_value if local_value is not None else local(),
        scan=scan_value, receipts=receipts, now=NOW,
    )


def test_old_present_is_only_history_and_never_authorizes_skip_or_removal():
    item, = preview()
    assert (item.desired, item.observed, item.last_known) == (
        "keep", "unknown", "present",
    )
    assert item.last_observed_at == NOW - 600
    assert item.action == "verify"
    assert item.reason == "device_observation_required"
    assert item.executable is False
    retired, = preview(placements=[intent(desired="remove")])
    assert retired.action == "verify"
    assert retired.observed == "unknown"
    assert retired.executable is False


@pytest.mark.parametrize("change", [
    {"fingerprint": "usb-other-mount"},
    {"device": "reader-other"},
    {"identity": "bob"},
    {"when": NOW - 301},
    {"when": NOW + 1},
    {"complete": False},
])
def test_wrong_stale_incomplete_or_foreign_scan_is_not_presence_evidence(change):
    report, = preview(scan_value=scan(**change))
    assert report.action == "verify"
    assert report.observed == "unknown"
    assert report.last_known == "present"


def test_exact_fresh_missing_is_only_an_upload_candidate_with_owned_sha():
    data = scan()
    report, = preview(scan_value=data)
    assert (report.action, report.observed) == ("upload_candidate", "missing")
    assert report.reason == "revalidate_before_usb_write"
    assert report.executable is False
    for owner, digest, verified in (
        ("bob", SHA_A, True), ("alice", "invalid", True),
        ("alice", SHA_A, False),
    ):
        row, = preview(
            scan_value=data,
            local_value=local(owner=owner, digest=digest, verified=verified),
        )
        assert (row.action, row.reason) == ("blocked", "owned_source_unverified")


def test_shas_match_before_present_is_rendered_as_verified_skip():
    evidence = scan(files={"artifact-a": UsbObservedFile(FILE_PATH, SHA_A, regular_file=True)})
    matching, = preview(scan_value=evidence)
    assert (matching.action, matching.observed) == ("skip", "present")
    conflict, = preview(scan_value=scan(
        files={"artifact-a": UsbObservedFile(FILE_PATH, SHA_B, regular_file=True)},
    ))
    assert conflict.action == "blocked"
    assert conflict.reason == "source_or_device_sha_unverified"
    assert conflict.executable is False


@pytest.mark.parametrize("bad_path", [
    "../escape.epub", "/root/file.epub", "Books//duplicate.epub",
    "Books/./title.epub", "C:/Windows/file.epub", "Books\\name.epub",
    "Books/Paris/../other.epub",
])
def test_invalid_relative_path_is_never_treated_as_managed(bad_path):
    state = scan(files={"artifact-a": UsbObservedFile(bad_path, SHA_A, regular_file=True)})
    result, = preview(scan_value=state)
    assert result.action == "verify"
    assert result.observed == "unknown"
    assert result.executable is False


def test_remove_requires_prior_exact_managed_write_receipt_and_new_confirmation():
    obs = scan(files={"artifact-a": UsbObservedFile(FILE_PATH, SHA_A, regular_file=True)})
    values = [intent(desired="remove")]
    no_proof, = preview(placements=values, scan_value=obs)
    assert (no_proof.action, no_proof.reason) == (
        "blocked", "unmanaged_or_changed_file",
    )
    good = UsbManagedReceipt("alice", "reader-a", FINGERPRINT, FILE_PATH, SHA_A)
    for changed in (
        replace(good, identity_id="bob"), replace(good, device_id="reader-b"),
        replace(good, fingerprint="usb-other"), replace(good, sha256=SHA_B),
        replace(good, relative_path="Books/Paris/another.epub"),
    ):
        bad, = preview(placements=values, scan_value=obs,
                       receipts={"artifact-a": changed})
        assert bad.action == "blocked"
    approved, = preview(
        placements=values, scan_value=obs, receipts={"artifact-a": good},
    )
    assert approved.action == "remove_candidate"
    assert approved.reason == "managed_removal_requires_confirmation"
    assert approved.executable is False
    missing, = preview(placements=values, scan_value=scan(),
                       receipts={"artifact-a": good})
    assert (missing.action, missing.observed) == ("skip", "missing")


@pytest.mark.parametrize("bad", [None, 1, 0, "true", "false", {}])
def test_device_must_be_explicitly_enabled_for_any_candidate(bad):
    item, = preview(target=binding(enabled=bad), scan_value=scan())
    assert (item.action, item.reason) == (
        "blocked", "device_disabled_or_unverified",
    )


def test_mismatched_binding_or_cross_device_rows_fail_closed_before_projection():
    with pytest.raises(ValueError, match="outside selected identity"):
        preview(identity="bob")
    with pytest.raises(ValueError, match="cross-device"):
        preview(placements=[intent(device="reader-other")], scan_value=scan())
    with pytest.raises(ValueError, match="cross-device"):
        preview(placements=[intent(), intent()], scan_value=scan())


def test_two_identities_and_three_devices_remain_independently_scoped(tmp_path):
    db = Storage(tmp_path / "library.sqlite3")
    for device_id, owner, desired in (
        ("reader-a", "alice", "keep"),
        ("reader-b", "alice", "remove"),
        ("reader-c", "bob", "keep"),
    ):
        db.device_binding_put({
            "device_id": device_id, "identity_id": owner,
            "metadata": {"usb_fingerprint": FINGERPRINT},
        })
        db.device_placement_put({
            "device_id": device_id, "artifact_id": "same-artifact",
            "desired_state": desired, "observed_state": "present",
            "last_observed_at": NOW - 3600,
            "target_relative_path": FILE_PATH,
            "metadata": {"intent_identity_id": owner},
        })
    before_bindings = deepcopy(db.device_bindings())
    before_placements = deepcopy(db.device_placements())
    for device_id, owner, expected in (
        ("reader-a", "alice", "upload_candidate"),
        ("reader-b", "alice", "skip"),
        ("reader-c", "bob", "blocked"),
    ):
        # No source row for Bob: a same-SHA artifact belonging to Alice is
        # not evidence of Bob's ownership or a new right to acquire it.
        existing = db.device_binding_get(device_id)
        rows = db.device_placements(device_id=device_id)
        sources = {
            "same-artifact": UsbLocalArtifact("alice", SHA_A, verified=True)
        }
        report = preview_usb_delta(
            identity_id=owner, binding=existing, placements=rows, local=sources,
            scan=scan(device=device_id, identity=owner), now=NOW,
        )
        assert len(report) == 1
        assert report[0].action == expected
        assert report[0].executable is False
    assert db.device_bindings() == before_bindings
    assert db.device_placements() == before_placements


def test_preview_does_not_mutate_caller_evidence_or_persistent_state():
    b, p = binding(), [intent()]
    state = scan(files={"artifact-a": UsbObservedFile(FILE_PATH, SHA_A, regular_file=True)})
    sources = local()
    receipts = {"artifact-a": UsbManagedReceipt(
        "alice", "reader-a", FINGERPRINT, FILE_PATH, SHA_A,
    )}
    before = deepcopy((b, p, state, sources, receipts))
    result = preview(
        target=b, placements=p, scan_value=state,
        local_value=sources, receipts=receipts,
    )
    assert result[0].action == "skip"
    assert (b, p, state, sources, receipts) == before


def test_legacy_or_reassigned_device_intent_cannot_leak_another_profile():
    for legacy in (None, {}, {"intent_identity_id": "bob"}):
        row = intent()
        row["metadata"] = legacy
        with pytest.raises(ValueError, match="unproven placement owner"):
            preview(placements=[row], scan_value=scan())

    # Device identity may have changed while an old placement survived.
    row = intent(owner="alice")
    with pytest.raises(ValueError, match="unproven placement owner"):
        preview(
            identity="bob",
            target=binding(identity="bob"),
            placements=[row],
            scan_value=scan(identity="bob"),
            local_value={"artifact-a": UsbLocalArtifact("bob", SHA_A, True)},
        )


def test_missing_local_artifact_is_blocked_without_claiming_new_rights():
    row, = preview(scan_value=scan(), local_value={})
    assert row.action == "blocked"
    assert row.reason == "owned_source_unverified"
    assert row.executable is False


@pytest.mark.parametrize("invalid", [
    "https://secret.invalid/?token=private", 3, True, None, [],
])
def test_malformed_intent_is_blocked_without_echoing_untrusted_text(invalid):
    row = intent()
    row["desired_state"] = invalid
    result, = preview(placements=[row], scan_value=scan())
    assert result.action == "blocked"
    assert result.desired == "unknown"
    assert result.reason == "invalid_intent"
    assert result.executable is False


@pytest.mark.parametrize("regular", [False, None, "true", 1])
def test_non_regular_usb_entries_cannot_attest_presence_or_absence(regular):
    observed = scan(files={
        "artifact-a": UsbObservedFile(FILE_PATH, SHA_A, regular_file=regular),
    })
    keep, = preview(scan_value=observed)
    remove, = preview(placements=[intent(desired="remove")], scan_value=observed)
    assert (keep.action, keep.observed) == ("verify", "unknown")
    assert (remove.action, remove.observed) == ("verify", "unknown")


def test_ambiguous_scanned_paths_invalidate_full_scan_including_absence():
    observed = scan(files={
        "artifact-external": UsbObservedFile(
            FILE_PATH, SHA_A, regular_file=True,
        ),
        "artifact-second": UsbObservedFile(
            "books/paris/A.epub", SHA_B, regular_file=True,
        ),
    })
    result, = preview(scan_value=observed)
    assert (result.action, result.observed) == ("verify", "unknown")
    assert result.executable is False


def test_unplanned_destination_never_proves_missing_or_present():
    for observed in (
        scan(), scan(files={
            "artifact-a": UsbObservedFile(FILE_PATH, SHA_A, regular_file=True),
        }),
    ):
        for desired in ("keep", "remove"):
            row = intent(desired=desired)
            row["target_relative_path"] = None
            shown, = preview(placements=[row], scan_value=observed)
            assert (shown.action, shown.observed) == ("verify", "unknown")
            assert shown.reason == "target_path_unqualified"
            assert shown.executable is False


@pytest.mark.parametrize("relative", [
    "", "../outside.epub", "Books/Paris/../escape.epub",
    "/media/user/Books/Paris/a.epub",
])
def test_unqualified_target_path_is_not_absence_evidence(relative):
    row = intent()
    row["target_relative_path"] = relative
    shown, = preview(placements=[row], scan_value=scan())
    assert (shown.action, shown.observed) == ("verify", "unknown")


def test_target_path_must_remain_within_declared_managed_directory():
    row = intent()
    row["target_relative_path"] = "Books/Foreign/a.epub"
    registered = binding()
    registered["target_directory"] = "Books/Paris"
    shown, = preview(placements=[row], target=registered, scan_value=scan())
    assert shown.action == "verify"
    assert shown.reason == "target_path_unqualified"
    registered["target_directory"] = {"malformed": True}
    shown, = preview(placements=[intent()], target=registered,
                     scan_value=scan())
    assert shown.action == "verify"


def test_observed_different_path_cannot_be_called_present_or_missing():
    seen = scan(files={
        "artifact-a": UsbObservedFile(
            "Books/Paris/another.epub", SHA_A, regular_file=True,
        ),
    })
    shown, = preview(scan_value=seen)
    assert (shown.action, shown.observed) == ("blocked", "unknown")
    assert shown.reason == "observed_target_path_mismatch"
    assert shown.executable is False


def test_external_file_occupying_target_blocks_upload_candidate():
    seen = scan(files={
        "untracked:external": UsbObservedFile(
            FILE_PATH, SHA_A, regular_file=True,
        ),
    })
    shown, = preview(scan_value=seen)
    assert (shown.action, shown.observed) == ("blocked", "unknown")
    assert shown.reason == "target_path_occupied"
    assert shown.executable is False
