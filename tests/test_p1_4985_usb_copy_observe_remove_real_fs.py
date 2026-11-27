"""P1 #4985: real EPUB bytes through the full Core USB copy/observe/remove cycle.

Test-only source policy is an *independent positive* synthetic authorization
over canonical SQLite provider provenance; it is not a real provider licence.
Transport append() stages real bytes via the production Core fd/rename path.
Only Linux USB bus/mount attestation and the policy issuer are synthetic.
The same real file is SHA-read back, observed, safely removed and re-observed.
"""
from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cifereca.core import device_usb_copy_recovery as recovery
from cifereca.core import device_usb_copy_supervisor as supervisor
from cifereca.core import device_usb_dry_run as dry_run
from cifereca.core import device_usb_execution_broker as broker_module
from cifereca.core import device_usb_library_copy as library_copy
from cifereca.core import device_usb_managed_scan as managed_scan
from cifereca.core import device_usb_observation_record as observations
from cifereca.core import device_usb_removal_execute as removal_execute
from cifereca.core import device_usb_removal_review as removal_review
from cifereca.core import device_usb_target_preflight as preflight
from cifereca.core.device_usb_operation_journal import (
    SqliteUsbOperationJournal, completed_usb_managed_copy_operations,
)
from cifereca.core.device_usb_sync_engine import UsbSyncResult
from cifereca.plugin_api import USB_STAGE_CAPABILITY
from tests.test_p1_2854_usb_copy_provenance import NOW
from tests.test_p1_2854_usb_live_source import live_args
from tests.test_p1_2854_usb_copy_preparation import copy_review


@pytest.fixture
def cycle(live_args, monkeypatch):
    args, active, source = live_args
    storage = args["storage"]
    target = args["mountpoint"]
    consent = replace(args["consent"], operation_id="a" * 32)
    final = target / consent.target_relative_path
    final.parent.mkdir(parents=True, exist_ok=True)
    clock = NS(time=lambda: NOW)
    observe = lambda **_: active["mount"]

    # Real managed filesystem scan, true inode + hash, real capacity, real
    # transport and Core journal; inject only bus identity for tmp_path.
    for mod in (
        managed_scan, preflight, broker_module, removal_review,
        removal_execute,
    ):
        monkeypatch.setattr(mod, "observe_linux_usb_mount", observe)
    monkeypatch.setattr(dry_run, "scan_managed_usb", managed_scan.scan_managed_usb)
    monkeypatch.setattr(dry_run, "preflight_usb_target", preflight.preflight_usb_target)
    monkeypatch.setattr(observations, "scan_managed_usb",
                        managed_scan.scan_managed_usb)
    for mod in (managed_scan, dry_run, library_copy, supervisor, removal_review,
                observations):
        monkeypatch.setattr(mod, "time", clock)
    monkeypatch.setattr(recovery, "_boot_id",
                        lambda: "15d003bd-94a9-4317-a221-292d3b465fe0")
    monkeypatch.setattr(recovery.linux_mount, "_fd_mount_id", lambda _: 52)

    native_open = os.open
    def open_synthetic_proc(path, flags, *positional, **kwargs):
        if isinstance(path, str) and path.startswith("/proc/self/fd/"):
            pinned = os.fstat(int(path.rsplit("/", 1)[-1]))
            for child in final.parent.iterdir():
                item = child.lstat()
                if (pinned.st_dev, pinned.st_ino) == (item.st_dev, item.st_ino):
                    return native_open(child, flags | os.O_NOFOLLOW)
            raise FileNotFoundError(path)
        return native_open(path, flags, *positional, **kwargs)
    monkeypatch.setattr(recovery.os, "open", open_synthetic_proc)

    monkeypatch.setattr(
        broker_module, "configured_identity_roots",
        lambda _config, identity: {identity: (source.parent,)},
    )

    journal = SqliteUsbOperationJournal(storage)
    # Core intentionally pins service *identity* across the whole write.
    # Returning a freshly constructed service on each registry read would
    # correctly revoke the pending transfer before the first staged byte.
    transport = NS(
        plugin_id="storage.usb", registry_generation=5,
        capabilities=(USB_STAGE_CAPABILITY,),
        service=NS(write_usb_stage=lambda payload, append: append(payload)),
    )
    manager = NS(
        registry_generation=5,
        services=lambda category: (transport,) if category == "storage.target" else (),
    )
    service = NS(
        current_identity=NS(identity_id="alice"), _account_context_epoch=3,
        storage=storage, config=object(), _plugin_manager=manager,
    )
    facade = broker_module.ReaderUsbExecutionBrokerImpl(service)
    entitled = [True]
    checked = []
    def independent_policy(witness):
        checked.append(witness.policy_ref)
        return (
            entitled[0]
            and witness.source_reference_id == "source:alice-exact"
            and witness.provider_id == "provider-one"
            and witness.account_id == "account-one"
            and witness.source_kind == "purchase"
            and witness.policy_ref == "provider-terms-copy-grant"
        )

    placed, = storage.placements(state="present")
    lock_root = target.parent / "live-cycle-locks"

    def copy_with(confirmation, *, local=False):
        return library_copy.execute_usb_library_copy(
            storage=storage, journal=journal, lock_root=lock_root,
            placement_id=placed["placement_id"], mountpoint=target,
            consent=confirmation, rights=None if local else args["rights"],
            current_profile=lambda: active["profile"],
            policy_verifier=None if local else independent_policy,
            plugin_manager=manager, plugin_id="storage.usb",
            registry_generation=5,
        )

    return NS(
        storage=storage, journal=journal, copy=copy_with, consent=consent,
        rights=args["rights"], checked=checked, entitled=entitled,
        source=source, final=final, mountpoint=target, facade=facade,
        service=service, active=active,
    )


def test_real_bytes_copy_observe_reject_second_copy_then_confirm_remove(cycle):
    rig = cycle
    payload = rig.source.read_bytes()
    assert not rig.final.exists()
    assert payload.startswith(b"PK")  # actual EPUB/ZIP, not a fake result
    before = rig.storage.device_placement_get(
        rig.consent.device_id, rig.consent.artifact_id,
    )
    assert before["desired_state"] == "keep"
    assert before["observed_state"] == "unknown"
    assert rig.journal.get(rig.consent.operation_id) is None

    copied = rig.copy(rig.consent)
    assert copied.status == "completed", (copied.status, copied.reason)
    assert rig.checked, "source copy policy must independently attest"
    assert rig.final.read_bytes() == payload
    assert rig.source.read_bytes() == payload
    assert rig.journal.get(rig.consent.operation_id).phase == "completed"
    observed = observations.record_verified_usb_observation(
        storage=rig.storage, identity_id="alice",
        device_id=rig.consent.device_id, mountpoint=rig.mountpoint,
        now=NOW,
    )
    assert observed.status == "recorded"
    assert rig.storage.device_placement_get(
        rig.consent.device_id, rig.consent.artifact_id,
    )["observed_state"] == "present"

    # Retrying the same explicit consent must not stage a second copy;
    # another operation ID cannot replace an occupied managed filename.
    assert rig.copy(rig.consent).status != "completed"
    new = replace(rig.consent, operation_id="c" * 32)
    assert rig.copy(new).status != "completed"
    assert rig.journal.get(new.operation_id) is None
    assert rig.final.read_bytes() == payload

    rig.storage.device_usb_intent_set(
        identity_id="alice", device_id=rig.consent.device_id,
        artifact_id=rig.consent.artifact_id, desired_state="remove",
    )
    assert completed_usb_managed_copy_operations(
        storage=rig.storage, identity_id="alice",
        device_id=rig.consent.device_id,
        fingerprint=rig.consent.usb_fingerprint,
    ) == {rig.consent.artifact_id: rig.consent.operation_id}
    from cifereca.core.device_usb_removal_review import UsbRemovalConsent
    removal = UsbRemovalConsent(
        "alice", rig.consent.device_id, rig.consent.usb_fingerprint,
        rig.consent.artifact_id, rig.consent.sha256,
        rig.consent.target_relative_path, rig.consent.operation_id,
        "b" * 32, NOW - 1, NOW + 60,
    )
    # User confirmation is represented by the distinct real consent;
    # the production GUI's two-stage GTK dialog is separately E2E-tested.
    result = rig.facade.execute_one(
        confirmation=removal, mountpoint=rig.mountpoint,
        expected_profile_epoch=3,
    )
    assert result == UsbSyncResult("completed", removed=1, observed=True)
    assert not rig.final.exists()
    assert rig.source.read_bytes() == payload
    assert rig.journal.get(removal.operation_id).phase == "completed"
    assert rig.storage.device_placement_get(
        rig.consent.device_id, rig.consent.artifact_id,
    )["observed_state"] == "missing"
    assert rig.facade.completed_copy_operation(
        identity_id="alice", device_id=rig.consent.device_id,
        artifact_id=rig.consent.artifact_id,
    ) is None


def test_rejected_independent_source_policy_never_starts_real_usb_copy(cycle):
    rig = cycle
    rig.entitled[0] = False
    assert rig.copy(rig.consent).status != "completed"
    assert rig.checked
    assert not rig.final.exists()
    assert rig.journal.get(rig.consent.operation_id) is None
    assert rig.storage.device_placement_get(
        rig.consent.device_id, rig.consent.artifact_id,
    )["observed_state"] == "unknown"
    assert rig.source.exists()


def test_local_copy_without_provider_rights_is_managed_and_removable(cycle):
    rig = cycle
    rig.entitled[0] = False
    payload = rig.source.read_bytes()
    copied = rig.copy(rig.consent, local=True)
    assert copied.status == "completed", (copied.status, copied.reason)
    assert not rig.checked
    assert rig.final.read_bytes() == payload
    assert rig.journal.get(rig.consent.operation_id).rights is None
    assert rig.copy(rig.consent, local=True).status != "completed"
    rig.storage.device_usb_intent_set(
        identity_id="alice", device_id=rig.consent.device_id,
        artifact_id=rig.consent.artifact_id, desired_state="remove",
    )
    assert completed_usb_managed_copy_operations(
        storage=rig.storage, identity_id="alice", device_id=rig.consent.device_id,
        fingerprint=rig.consent.usb_fingerprint,
    ) == {rig.consent.artifact_id: rig.consent.operation_id}
    from cifereca.core.device_usb_removal_review import UsbRemovalConsent
    removal = UsbRemovalConsent(
        "alice", rig.consent.device_id, rig.consent.usb_fingerprint,
        rig.consent.artifact_id, rig.consent.sha256,
        rig.consent.target_relative_path, rig.consent.operation_id,
        "b" * 32, NOW - 1, NOW + 60,
    )
    result = rig.facade.execute_one(
        confirmation=removal, mountpoint=rig.mountpoint, expected_profile_epoch=3,
    )
    assert result == UsbSyncResult("completed", removed=1, observed=True)
    assert not rig.final.exists()
    assert rig.source.read_bytes() == payload
