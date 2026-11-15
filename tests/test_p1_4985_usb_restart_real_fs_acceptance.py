"""P1 #4985: durable real-file USB withdrawal across Core/SQLite restarts.

The Linux bus+mount attestation is injected only for the disposable fixture.
All copied bytes, SQLite operation records, managed file SHA inspection,
review, locks, unlink and final observation use the production implementation.
No provider or physical reader is contacted.
"""
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace as NS

from cifereca.core import device_usb_execution_broker as execution
from cifereca.core.device_usb_operation_journal import (
    SqliteUsbOperationJournal, completed_usb_managed_copy_operations,
)
from cifereca.core.device_usb_sync_engine import UsbSyncResult
from cifereca.storage import Storage
from tests.test_p1_4985_usb_broker_real_fs_acceptance import live_broker
from tests.test_p1_2854_usb_removal_review import completed_copy
from tests.test_p1_2854_usb_live_source import live_args
from tests.test_p1_2854_usb_copy_preparation import copy_review


def reopened_broker(rig):
    """A new process-like storage/facade, without reusing a cached receipt."""
    storage = Storage(rig.storage.path)
    service = NS(
        current_identity=NS(identity_id="alice"),
        _account_context_epoch=12,
        storage=storage, config=object(),
        _plugin_manager=NS(registry_generation=5),
    )
    return storage, execution.ReaderUsbExecutionBrokerImpl(service)


def test_completed_copy_receipt_survives_restart_and_exact_removal_is_durable(
    live_broker,
):
    rig = live_broker
    expected = rig.final.read_bytes()
    source = rig.source.read_bytes()
    assert expected == source
    consent = rig.consent
    assert rig.storage.device_placement_get(
        consent.device_id, consent.artifact_id,
    )["desired_state"] == "remove"
    rig.storage.close()

    second, facade = reopened_broker(rig)
    try:
        receipt = SqliteUsbOperationJournal(second).get(consent.copy_operation_id)
        assert receipt.phase == "completed"
        assert completed_usb_managed_copy_operations(
            storage=second, identity_id="alice", device_id=consent.device_id,
            fingerprint=consent.usb_fingerprint,
        ) == {consent.artifact_id: consent.copy_operation_id}
        preview = facade.preview(
            identity_id="alice", device_id=consent.device_id,
            mountpoint=rig.mountpoint,
        )
        assert preview.status == "scanned" and preview.scan_attested is True
        assert len(preview.rows) == 1
        assert preview.rows[0].action == "remove_candidate"
        assert facade.completed_copy_operation(
            identity_id="alice", device_id=consent.device_id,
            artifact_id=consent.artifact_id,
        ) == consent.copy_operation_id

        # This is the actual production locked unlink and absence recorder.
        result = facade.execute_one(
            confirmation=consent, mountpoint=rig.mountpoint,
            expected_profile_epoch=12,
        )
        assert result == UsbSyncResult("completed", removed=1, observed=True)
        assert not rig.final.exists()
        assert rig.source.read_bytes() == source
        assert SqliteUsbOperationJournal(second).get(consent.operation_id).phase == "completed"
        assert second.device_placement_get(
            consent.device_id, consent.artifact_id,
        )["observed_state"] == "missing"
    finally:
        second.close()

    # A further restart cannot resurrect the old managed copy receipt or
    # replay its deletion, even if the same filename has been reused.
    third, next_facade = reopened_broker(rig)
    try:
        assert completed_usb_managed_copy_operations(
            storage=third, identity_id="alice", device_id=consent.device_id,
            fingerprint=consent.usb_fingerprint,
        ) == {}
        assert third.device_placement_get(
            consent.device_id, consent.artifact_id,
        )["observed_state"] == "missing"
        rig.final.write_bytes(b"new foreign file at former managed target")
        assert next_facade.execute_one(
            confirmation=consent, mountpoint=rig.mountpoint,
            expected_profile_epoch=12,
        ).status != "completed"
        assert rig.final.read_bytes() == b"new foreign file at former managed target"
        assert rig.source.read_bytes() == source
    finally:
        third.close()


def test_offline_restart_does_not_turn_cached_receipt_into_usb_presence(
    live_broker,
):
    rig = live_broker
    consent = rig.consent
    original = rig.final.read_bytes()
    source = rig.source.read_bytes()
    rig.storage.close()
    second, facade = reopened_broker(rig)
    before_mount = rig.active["mount"]
    try:
        rig.active["mount"] = None
        # A durable copy receipt is history, not proof of an attached volume.
        assert facade.preview(
            identity_id="alice", device_id=consent.device_id,
            mountpoint=rig.mountpoint,
        ).status == "unavailable"
        assert facade.execute_one(
            confirmation=consent, mountpoint=rig.mountpoint,
            expected_profile_epoch=12,
        ).status != "completed"
        assert rig.final.read_bytes() == original
        assert rig.source.read_bytes() == source
        assert SqliteUsbOperationJournal(second).get(consent.operation_id) is None
        assert second.device_placement_get(
            consent.device_id, consent.artifact_id,
        )["observed_state"] == "unknown"

        # Once the same attested mount becomes available again, an explicit
        # request can re-scan and decide without implicit offline mutation.
        rig.active["mount"] = before_mount
        review = facade.preview(
            identity_id="alice", device_id=consent.device_id,
            mountpoint=rig.mountpoint,
        )
        assert review.status == "scanned" and review.rows[0].action == "remove_candidate"
        assert rig.final.read_bytes() == original
        assert SqliteUsbOperationJournal(second).get(consent.operation_id) is None
    finally:
        rig.active["mount"] = before_mount
        second.close()


def test_remount_after_restart_invalidates_old_inode_pin_and_consent(
    live_broker,
):
    rig = live_broker
    consent = rig.consent
    before = rig.final.read_bytes()
    rig.storage.close()
    second, facade = reopened_broker(rig)
    original = rig.active["mount"]
    try:
        rig.active["mount"] = replace(original, mount_id=original.mount_id + 1)
        refused = facade.execute_one(
            confirmation=consent, mountpoint=rig.mountpoint,
            expected_profile_epoch=12,
        )
        assert refused.status != "completed"
        assert rig.final.read_bytes() == before
        assert SqliteUsbOperationJournal(second).get(consent.operation_id) is None
        assert rig.source.read_bytes() == before
    finally:
        rig.active["mount"] = original
        second.close()
