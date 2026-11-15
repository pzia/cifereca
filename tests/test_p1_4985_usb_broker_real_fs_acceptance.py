"""#4985: real Core USB removal through the public broker on a fake volume.

No real USB device, external provider or user secret is involved. This
acceptance exercises the *real* managed-file scanner, production operation
journal, read-only broker admission, locked removal executor and post-removal
observation against actual bytes on a disposable filesystem.

An initial synthetic, explicitly authorized Core write is supplied by the
existing completed_copy fixture. The already-attested copy receipt is
migrated into the canonical journal as test setup only; production admission
still demands an exact completed journal receipt and physical inode.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace as NS
import time

import pytest

from cifereca.core import device_usb_execution_broker as broker_module
from cifereca.core import device_usb_dry_run as dry_run
from cifereca.core import device_usb_managed_scan as managed_scan
from cifereca.core import device_usb_removal_execute as removal_execute
from cifereca.core import device_usb_removal_review as removal_review
from cifereca.core import device_usb_observation_record as observation_record
from cifereca.core.device_usb_operation_journal import (
    SqliteUsbOperationJournal, completed_usb_managed_copy_operations,
    completed_usb_managed_receipts,
)
from cifereca.core.device_usb_sync_engine import UsbSyncResult
from tests.test_p1_2854_usb_removal_review import completed_copy
from tests.test_p1_2854_usb_live_source import live_args
from tests.test_p1_2854_usb_copy_preparation import copy_review


@pytest.fixture
def live_broker(completed_copy, monkeypatch):
    args, active, final, source = completed_copy
    storage = args["storage"]
    mountpoint = args["mountpoint"]
    copy_id = args["consent"].copy_operation_id
    copied = args["journal"].get(copy_id)
    assert copied is not None and copied.phase == "completed"
    production = SqliteUsbOperationJournal(storage)
    # Feed the canonical journal the exact finished proof and inode already
    # produced by the real supervisor fixture, with no invented receipt.
    seeded = replace(copied, phase="pending")
    assert production.begin(seeded) is True
    assert production.compare_and_set(seeded, copied) is True
    assert production.get(copy_id) == copied
    assert final.read_bytes() == source.read_bytes()

    # Fake Linux only at the block-device boundary: all filesystem walks,
    # SHA checks, operation CAS and unlink remain the actual Core code.
    observe = lambda **_: active["mount"]
    for module in (
        broker_module, managed_scan, removal_review, removal_execute,
    ):
        monkeypatch.setattr(module, "observe_linux_usb_mount", observe)
    monkeypatch.setattr(dry_run, "scan_managed_usb", managed_scan.scan_managed_usb)
    monkeypatch.setattr(observation_record, "scan_managed_usb",
                        managed_scan.scan_managed_usb)
    root = source.parent
    monkeypatch.setattr(
        broker_module, "configured_identity_roots",
        lambda _config, identity: {identity: (root,)},
    )
    identity = NS(identity_id="alice")
    service = NS(
        current_identity=identity, _account_context_epoch=3,
        storage=storage, config=object(),
        _plugin_manager=NS(registry_generation=5),
    )
    facade = broker_module.ReaderUsbExecutionBrokerImpl(service)
    consent = replace(
        args["consent"], confirmed_at=int(time.time()) - 1,
        expires_at=int(time.time()) + 60,
    )
    data = NS(
        storage=storage, facade=facade, service=service,
        consent=consent, final=final, source=source,
        active=active, mountpoint=mountpoint, journal=production,
    )
    return data


def test_real_broker_removes_only_receipted_usb_copy_and_records_absence(live_broker):
    rig = live_broker
    consent = rig.consent
    source_bytes = rig.source.read_bytes()
    before = rig.final.read_bytes()
    assert before == source_bytes
    assert completed_usb_managed_copy_operations(
        storage=rig.storage, identity_id=consent.identity_id,
        device_id=consent.device_id, fingerprint=consent.usb_fingerprint,
    ) == {consent.artifact_id: consent.copy_operation_id}
    report = rig.facade.preview(
        identity_id=consent.identity_id, device_id=consent.device_id,
        mountpoint=rig.mountpoint,
    )
    assert report.status == "scanned" and report.scan_attested
    assert len(report.rows) == 1
    assert report.rows[0].action == "remove_candidate"
    assert report.rows[0].executable is False
    assert rig.facade.completed_copy_operation(
        identity_id=consent.identity_id, device_id=consent.device_id,
        artifact_id=consent.artifact_id,
    ) == consent.copy_operation_id

    result = rig.facade.execute_one(
        confirmation=consent, mountpoint=rig.mountpoint,
        expected_profile_epoch=3,
    )
    assert result == UsbSyncResult("completed", removed=1, observed=True)
    assert not rig.final.exists()
    assert rig.source.read_bytes() == source_bytes
    assert rig.journal.get(consent.operation_id).phase == "completed"
    assert rig.journal.get(consent.copy_operation_id).phase == "completed"
    assert completed_usb_managed_copy_operations(
        storage=rig.storage, identity_id=consent.identity_id,
        device_id=consent.device_id, fingerprint=consent.usb_fingerprint,
    ) == {}
    saved = rig.storage.device_placement_get(consent.device_id, consent.artifact_id)
    assert saved["desired_state"] == "remove"
    assert saved["observed_state"] == "missing"

    # Consent is consumed; an old request cannot erase any new foreign file.
    rig.final.write_bytes(b"unrelated replacement")
    assert rig.facade.execute_one(
        confirmation=consent, mountpoint=rig.mountpoint,
        expected_profile_epoch=3,
    ).status != "completed"
    assert rig.final.read_bytes() == b"unrelated replacement"
    assert rig.source.read_bytes() == source_bytes


@pytest.mark.parametrize("change", ["same_bytes_new_inode", "modified_bytes",
                                     "remounted", "profile_epoch", "cancelled"])
def test_real_broker_never_unlinks_if_proof_revoked(live_broker, change):
    rig = live_broker
    if change == "same_bytes_new_inode":
        previous = rig.final.with_suffix(".old")
        rig.final.rename(previous)
        rig.final.write_bytes(previous.read_bytes())
    elif change == "modified_bytes":
        rig.final.write_bytes(b"foreign bytes")
    elif change == "remounted":
        rig.active["mount"] = replace(rig.active["mount"], mount_id=53)
    elif change == "profile_epoch":
        rig.service._account_context_epoch += 1
    else:
        rig.storage.device_usb_intent_set(
            identity_id=rig.consent.identity_id, device_id=rig.consent.device_id,
            artifact_id=rig.consent.artifact_id, desired_state="keep",
        )
    actual = rig.final.read_bytes()
    result = rig.facade.execute_one(
        confirmation=rig.consent, mountpoint=rig.mountpoint,
        expected_profile_epoch=3,
    )
    assert result.status != "completed"
    assert rig.final.read_bytes() == actual
    assert rig.source.exists()
    assert rig.journal.get(rig.consent.operation_id) is None
