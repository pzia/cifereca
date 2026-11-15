"""P1 #4985: two USB readers on one desktop, real files, separate receipts.

Use the existing completed Core copy fixture and production SQLite journal.
Only the Linux USB block-volume attestation is synthetic; the USB scanner,
SHA, inode checks, journal, locks and physical unlink remain production code.
The second reader deliberately holds identical bytes at the same relative
path, but no Core-managed write receipt for that *device and UUID*.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from cifereca.core import device_usb_execution_broker as broker_module
from cifereca.core import device_usb_managed_scan as managed_scan
from cifereca.core import device_usb_removal_execute as removal_execute
from cifereca.core import device_usb_removal_review as removal_review
from cifereca.core.device_usb_sync_engine import UsbSyncResult
from tests.test_p1_4985_usb_broker_real_fs_acceptance import live_broker
from tests.test_p1_2854_usb_removal_review import completed_copy
from tests.test_p1_2854_usb_live_source import live_args
from tests.test_p1_2854_usb_copy_preparation import copy_review


@pytest.fixture
def two_readers(live_broker, monkeypatch):
    rig = live_broker
    storage = rig.storage
    source_bytes = rig.source.read_bytes()
    target = rig.consent.target_relative_path

    storage.device_usb_binding_declare(
        identity_id="alice", device_id="reader-b", label="Second reader",
        usb_fingerprint="uuid-reader-b", enabled=True,
    )
    storage.device_usb_reader_formats_set(
        identity_id="alice", device_id="reader-b",
        usb_fingerprint="uuid-reader-b", supported_formats=("epub",),
    )
    storage.device_usb_intent_set(
        identity_id="alice", device_id="reader-b",
        artifact_id=rig.consent.artifact_id, desired_state="keep",
    )
    storage.device_usb_target_set(
        identity_id="alice", device_id="reader-b",
        artifact_id=rig.consent.artifact_id, target_relative_path=target,
    )
    storage.device_usb_intent_set(
        identity_id="alice", device_id="reader-b",
        artifact_id=rig.consent.artifact_id, desired_state="remove",
    )

    volume_b = rig.mountpoint.parent / "reader-b-mounted"
    target_b = volume_b / target
    target_b.parent.mkdir(parents=True, exist_ok=True)
    target_b.write_bytes(source_bytes)

    mount_a = rig.active["mount"]
    mount_b = replace(
        mount_a, device_id="reader-b", usb_fingerprint="uuid-reader-b",
        mountpoint=volume_b,
    )

    def attest(*, identity_id, binding, mountpoint):
        if identity_id != "alice":
            return None
        if binding.get("device_id") == "reader-a" and mountpoint == rig.mountpoint:
            return rig.active["mount"]
        if binding.get("device_id") == "reader-b" and mountpoint == volume_b:
            return mount_b
        return None

    for module in (broker_module, managed_scan, removal_review, removal_execute):
        monkeypatch.setattr(module, "observe_linux_usb_mount", attest)

    rogue_consent = replace(
        rig.consent, device_id="reader-b", usb_fingerprint="uuid-reader-b",
        operation_id="c" * 32,
    )
    return rig, volume_b, target_b, rogue_consent


def test_second_reader_same_bytes_and_path_cannot_claim_first_receipt(two_readers):
    rig, volume_b, target_b, rogue = two_readers
    original_a = rig.final.read_bytes()
    original_b = target_b.read_bytes()
    assert original_a == original_b == rig.source.read_bytes()

    # Same artifact and same managed relative destination in a separate USB
    # volume do not grant the second reader ownership of the original copy.
    assert rig.facade.completed_copy_operation(
        identity_id="alice", device_id="reader-b",
        artifact_id=rogue.artifact_id,
    ) is None
    preview = rig.facade.preview(
        identity_id="alice", device_id="reader-b", mountpoint=volume_b,
    )
    assert preview.status == "scanned" and preview.scan_attested is True
    assert all(row.action != "remove_candidate" for row in preview.rows)
    denied = rig.facade.execute_one(
        confirmation=rogue, mountpoint=volume_b, expected_profile_epoch=3,
    )
    assert denied.status != "completed"
    assert rig.journal.get(rogue.operation_id) is None
    assert rig.final.read_bytes() == original_a
    assert target_b.read_bytes() == original_b
    assert rig.source.read_bytes() == original_a

    # A later valid deletion of reader A cannot remove B's identical copy.
    accepted = rig.facade.execute_one(
        confirmation=rig.consent, mountpoint=rig.mountpoint,
        expected_profile_epoch=3,
    )
    assert accepted == UsbSyncResult("completed", removed=1, observed=True)
    assert not rig.final.exists()
    assert target_b.read_bytes() == original_b
    assert rig.source.read_bytes() == original_a
    assert storage_a_state(rig) == "missing"
    assert rig.storage.device_placement_get("reader-b", rogue.artifact_id)[
        "desired_state"
    ] == "remove"


def storage_a_state(rig):
    return rig.storage.device_placement_get(
        rig.consent.device_id, rig.consent.artifact_id,
    )["observed_state"]


@pytest.mark.parametrize("swap", [
    "first_consent_second_volume",
    "second_consent_first_volume",
    "foreign_profile",
    "volume_swap_before_execute",
])
def test_two_reader_mount_or_profile_mismatch_never_unlinks(
    two_readers, swap,
):
    rig, volume_b, target_b, rogue = two_readers
    if swap == "first_consent_second_volume":
        consent, volume = rig.consent, volume_b
    elif swap == "second_consent_first_volume":
        consent, volume = rogue, rig.mountpoint
    elif swap == "foreign_profile":
        rig.service.current_identity = type(rig.service.current_identity)(
            identity_id="bob",
        )
        consent, volume = rig.consent, rig.mountpoint
    else:
        # A real reconnect can change mount generation even at the same path.
        rig.active["mount"] = replace(rig.active["mount"], mount_id=999)
        consent, volume = rig.consent, rig.mountpoint

    before_a, before_b = rig.final.read_bytes(), target_b.read_bytes()
    result = rig.facade.execute_one(
        confirmation=consent, mountpoint=volume, expected_profile_epoch=3,
    )
    assert result.status != "completed"
    assert rig.final.read_bytes() == before_a
    assert target_b.read_bytes() == before_b
    assert rig.journal.get(consent.operation_id) is None


def test_profile_aba_does_not_authorize_foreign_reader_after_switch(two_readers):
    rig, volume_b, target_b, rogue = two_readers
    rig.service.current_identity = type(rig.service.current_identity)(
        identity_id="bob",
    )
    rig.service._account_context_epoch += 1
    rig.service.current_identity = type(rig.service.current_identity)(
        identity_id="alice",
    )
    rig.service._account_context_epoch += 1

    before = rig.final.read_bytes()
    assert rig.facade.execute_one(
        confirmation=rig.consent, mountpoint=rig.mountpoint,
        expected_profile_epoch=3,
    ).status == "blocked"
    assert rig.final.read_bytes() == before
    assert target_b.exists()
    assert rig.journal.get(rig.consent.operation_id) is None
