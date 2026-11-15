"""P1 #4985: a shared desktop/SQLite never shares USB deletion receipts.

Both profiles own local files with identical SHA, and both USB directories
hold identical real bytes at the same managed relative path. Core credentials,
device bindings and completed-copy journal entries remain scoped to the exact
identity, device and UUID. Cache sharing is unaffected and not emulated here.
Only Linux block-device attestation is synthesized for the two tmp volumes.
"""
from __future__ import annotations

from dataclasses import replace
import json
from types import SimpleNamespace as NS

import pytest

from cifereca.core import device_usb_execution_broker as broker_module
from cifereca.core import device_usb_managed_scan as managed_scan
from cifereca.core import device_usb_removal_review as removal_review
from cifereca.core import device_usb_removal_execute as removal_execute
from cifereca.core.device_usb_operation_journal import (
    completed_usb_managed_copy_operations,
)
from tests.test_p1_4985_usb_broker_real_fs_acceptance import live_broker
from tests.test_p1_2854_usb_removal_review import completed_copy
from tests.test_p1_2854_usb_live_source import live_args
from tests.test_p1_2854_usb_copy_preparation import copy_review


@pytest.fixture
def two_profiles(live_broker, tmp_path, monkeypatch):
    rig = live_broker
    c = rig.consent
    bytes_a = rig.source.read_bytes()
    bob_root = tmp_path / "bob-private"
    bob_root.mkdir()
    bob_source = bob_root / "same.epub"
    bob_source.write_bytes(bytes_a)
    rig.storage.placement_put({
        "artifact_id": c.artifact_id, "path": str(bob_source),
        "identity_id": "bob", "provider_id": "local",
        "source_item_id": "bob-owned-copy",
        "state": "present", "metadata": {"artifact_kind": "file"},
    })
    rig.storage.device_usb_binding_declare(
        identity_id="bob", device_id="reader-bob", label="Reader Bob",
        usb_fingerprint="uuid-bob-0001", enabled=True,
    )
    rig.storage.device_usb_reader_formats_set(
        identity_id="bob", device_id="reader-bob",
        usb_fingerprint="uuid-bob-0001", supported_formats=("epub",),
    )
    rig.storage.device_usb_intent_set(
        identity_id="bob", device_id="reader-bob",
        artifact_id=c.artifact_id, desired_state="keep",
    )
    rig.storage.device_usb_target_set(
        identity_id="bob", device_id="reader-bob",
        artifact_id=c.artifact_id,
        target_relative_path=c.target_relative_path,
    )
    rig.storage.device_usb_intent_set(
        identity_id="bob", device_id="reader-bob",
        artifact_id=c.artifact_id, desired_state="remove",
    )

    volume_b = tmp_path / "bob-volume"
    destination_b = volume_b / c.target_relative_path
    destination_b.parent.mkdir(parents=True)
    destination_b.write_bytes(bytes_a)
    mount_a = rig.active["mount"]
    mount_b = replace(
        mount_a, identity_id="bob", device_id="reader-bob",
        usb_fingerprint="uuid-bob-0001", mountpoint=volume_b,
    )

    def attest(*, identity_id, binding, mountpoint):
        if (identity_id == "alice"
                and binding.get("device_id") == c.device_id
                and mountpoint == rig.mountpoint):
            return rig.active["mount"]
        if (identity_id == "bob"
                and binding.get("device_id") == "reader-bob"
                and mountpoint == volume_b):
            return mount_b
        return None

    for module in (broker_module, managed_scan, removal_review, removal_execute):
        monkeypatch.setattr(module, "observe_linux_usb_mount", attest)
    monkeypatch.setattr(
        broker_module, "configured_identity_roots",
        lambda _config, identity: {
            identity: (
                rig.source.parent if identity == "alice"
                else bob_root,
            ),
        },
    )
    bob_service = NS(
        current_identity=NS(identity_id="bob"),
        _account_context_epoch=7,
        storage=rig.storage, config=object(),
        _plugin_manager=NS(registry_generation=5),
    )
    bob_broker = broker_module.ReaderUsbExecutionBrokerImpl(bob_service)
    consent_b = replace(
        c, identity_id="bob", device_id="reader-bob",
        usb_fingerprint="uuid-bob-0001", operation_id="c" * 32,
    )
    return NS(
        alice=rig, bob=bob_broker, bob_service=bob_service,
        bob_source=bob_source, bob_volume=volume_b,
        bob_file=destination_b, bob_consent=consent_b,
    )


def test_identical_files_on_two_profiles_never_share_copy_receipt_or_unlink(
    two_profiles,
):
    t = two_profiles
    a, b = t.alice, t.bob_consent
    source_bytes = a.source.read_bytes()
    assert a.final.read_bytes() == t.bob_file.read_bytes() == source_bytes
    assert t.bob_source.read_bytes() == source_bytes

    # Canonical journal is shared at the desktop/Storage level, but its
    # proof coordinates may never be reassigned across identities/devices.
    assert completed_usb_managed_copy_operations(
        storage=a.storage, identity_id="bob", device_id="reader-bob",
        fingerprint="uuid-bob-0001",
    ) == {}
    assert t.bob.completed_copy_operation(
        identity_id="bob", device_id="reader-bob",
        artifact_id=b.artifact_id,
    ) is None
    report = t.bob.preview(
        identity_id="bob", device_id="reader-bob",
        mountpoint=t.bob_volume,
    )
    assert report.status == "scanned" and report.scan_attested is True
    assert all(row.action != "remove_candidate" for row in report.rows)

    denied = t.bob.execute_one(
        confirmation=b, mountpoint=t.bob_volume,
        expected_profile_epoch=7,
    )
    assert denied.status != "completed"
    assert a.journal.get(b.operation_id) is None
    assert a.final.read_bytes() == t.bob_file.read_bytes() == source_bytes

    # Alice's independently receipted copy can still be withdrawn, but
    # neither Bob's copy nor either identity-owned local source is touched.
    done = a.facade.execute_one(
        confirmation=a.consent, mountpoint=a.mountpoint,
        expected_profile_epoch=3,
    )
    assert done.status == "completed" and done.removed == 1 and done.observed
    assert not a.final.exists()
    assert t.bob_file.read_bytes() == source_bytes
    assert a.source.read_bytes() == t.bob_source.read_bytes() == source_bytes
    assert a.storage.device_placement_get(
        "reader-bob", b.artifact_id,
    )["desired_state"] == "remove"


def test_storage_refuses_duplicate_uuid_across_profiles_atomically(
    two_profiles,
):
    t = two_profiles
    a, b = t.alice, t.bob_consent
    original_a, original_b = a.final.read_bytes(), t.bob_file.read_bytes()
    with pytest.raises(ValueError, match="USB volume already bound"):
        a.storage.device_usb_binding_declare(
            identity_id="bob", device_id="reader-bob", label="Reader Bob",
            usb_fingerprint=a.consent.usb_fingerprint, enabled=True,
        )
    assert a.storage.device_binding_get("reader-bob")[
        "metadata"
    ]["usb_fingerprint"] == "uuid-bob-0001"
    assert a.facade.completed_copy_operation(
        identity_id="alice", device_id=a.consent.device_id,
        artifact_id=a.consent.artifact_id,
    ) == a.consent.copy_operation_id
    assert a.final.read_bytes() == original_a
    assert t.bob_file.read_bytes() == original_b
    assert a.journal.get(a.consent.operation_id) is None
    assert a.journal.get(b.operation_id) is None


def test_corrupt_legacy_duplicate_uuid_blocks_both_core_brokers(
    two_profiles,
):
    """A malformed historical SQLite row cannot bypass Core uniqueness."""
    t = two_profiles
    a, b = t.alice, t.bob_consent
    original_a, original_b = a.final.read_bytes(), t.bob_file.read_bytes()
    # Deliberately bypass the public declaration guard to simulate a
    # pre-existing corrupted/legacy database, not a normal Settings save.
    with a.storage._lock, a.storage._connect() as db:
        row = db.execute(
            "SELECT metadata_json FROM device_bindings WHERE device_id=?",
            ("reader-bob",),
        ).fetchone()
        metadata = json.loads(row[0])
        metadata["usb_fingerprint"] = a.consent.usb_fingerprint
        db.execute(
            "UPDATE device_bindings SET metadata_json=? WHERE device_id=?",
            (json.dumps(metadata), "reader-bob"),
        )
    assert a.facade.completed_copy_operation(
        identity_id="alice", device_id=a.consent.device_id,
        artifact_id=a.consent.artifact_id,
    ) is None
    assert a.facade.execute_one(
        confirmation=a.consent, mountpoint=a.mountpoint,
        expected_profile_epoch=3,
    ).status != "completed"
    assert t.bob.execute_one(
        confirmation=replace(
            b, usb_fingerprint=a.consent.usb_fingerprint,
        ),
        mountpoint=t.bob_volume, expected_profile_epoch=7,
    ).status != "completed"
    assert a.final.read_bytes() == original_a
    assert t.bob_file.read_bytes() == original_b
    assert a.journal.get(a.consent.operation_id) is None
    assert a.journal.get(b.operation_id) is None
