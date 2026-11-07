"""Core live copy proof over real source bytes and canonical SQLite facts."""
from dataclasses import replace
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from cifereca.core import device_usb_live_source as live
from cifereca.core import device_usb_copy_recovery as recovery
from cifereca.core import device_usb_copy_supervisor as supervisor
from cifereca.core import device_usb_dry_run as dry_run
from cifereca.core import device_usb_library_copy as library_copy
from cifereca.core import device_usb_managed_scan as managed_scan
from cifereca.core import device_usb_target_preflight as preflight
from cifereca.core.device_usb_mount import LinuxUsbMount
from tests.test_p1_2854_usb_copy_preparation import copy_review
from cifereca.plugin_api import USB_STAGE_CAPABILITY
from tests.test_p1_2854_usb_copy_provenance import NOW
from tests.test_p1_2854_usb_copy_supervisor import SqliteTestJournal


@pytest.fixture
def live_args(copy_review, monkeypatch):
    db = copy_review["storage"]
    placement, = db.placements(state="present")
    root = copy_review["identity_root"]
    mountpoint = copy_review["mountpoint"]
    device = mountpoint.stat().st_dev
    mount = LinuxUsbMount(
        "alice", "reader-a", copy_review["rights"].usb_fingerprint,
        mountpoint, "ext4", os.major(device), os.minor(device), NOW, 52,
    )
    active = {"profile": ("alice", root, 3), "mount": mount}
    monkeypatch.setattr(live, "time", SimpleNamespace(time=lambda: NOW))
    monkeypatch.setattr(live, "observe_linux_usb_mount", lambda **_: active["mount"])
    args = dict(
        storage=db, placement_id=placement["placement_id"], mountpoint=mountpoint,
        consent=copy_review["consent"], rights=copy_review["rights"],
        current_profile=lambda: active["profile"], policy_verifier=lambda _: True,
    )
    return args, active, root / "book.epub"


def test_prepare_real_bytes_and_revalidate_exact_live_source(live_args):
    args, active, path = live_args
    before = args["storage"].device_placements()
    lease = live.prepare_usb_live_source(**args)
    assert lease is not None
    assert lease.payload == path.read_bytes()
    assert lease.mount == active["mount"]
    assert lease.revalidate(args["consent"], args["rights"]) is True
    assert args["storage"].device_placements() == before
    assert list(args["mountpoint"].iterdir()) == []


@pytest.mark.parametrize("change", ["profile_generation", "volume_generation", "same_bytes_new_inode", "intent"])
def test_scope_change_invalidates_live_source(live_args, change):
    args, active, path = live_args
    lease = live.prepare_usb_live_source(**args)
    assert lease is not None
    if change == "profile_generation":
        active["profile"] = (*active["profile"][:2], 4)
    elif change == "volume_generation":
        active["mount"] = replace(active["mount"], mount_id=53)
    elif change == "same_bytes_new_inode":
        old = path.with_suffix(".old")
        path.rename(old)
        path.write_bytes(old.read_bytes())
    else:
        args["storage"].device_usb_intent_set(
            identity_id="alice", device_id="reader-a",
            artifact_id=args["consent"].artifact_id, desired_state="remove",
        )
    assert lease.revalidate(args["consent"], args["rights"]) is False


def test_profile_switch_away_and_back_during_policy_is_detected(live_args):
    args, active, _ = live_args
    root = active["profile"][1]

    def change_profile(_):
        active["profile"] = ("bob", root, 4)
        active["profile"] = ("alice", root, 5)
        return True

    assert live.prepare_usb_live_source(**(args | {"policy_verifier": change_profile})) is None


def test_database_aba_during_policy_is_detected(live_args):
    args, _, _ = live_args
    storage = args["storage"]

    def change_intent(_):
        with storage._lock, storage._connect() as db:
            db.execute("UPDATE device_placements SET desired_state='remove'")
            db.execute("UPDATE device_placements SET desired_state='keep'")
        return True

    assert live.prepare_usb_live_source(**(args | {"policy_verifier": change_intent})) is None


def test_policy_revoked_after_preparation_and_mismatched_operation_are_refused(live_args):
    args, _, _ = live_args
    allowed = [True]
    lease = live.prepare_usb_live_source(**(args | {"policy_verifier": lambda _: allowed[0]}))
    assert lease is not None
    consent, rights = args["consent"], args["rights"]
    assert lease.revalidate(replace(consent, operation_id="another-confirmation"), rights) is False
    assert lease.revalidate(consent, replace(rights, account_id="other")) is False
    allowed[0] = False
    assert lease.revalidate(consent, rights) is False


def test_wrong_placement_never_reaches_rights_policy(live_args):
    args, _, _ = live_args
    result = live.prepare_usb_live_source(**(args | {
        "placement_id": "foreign-placement",
        "policy_verifier": lambda _: pytest.fail("wrong source should be rejected"),
    }))
    assert result is None


@pytest.mark.parametrize("relocated", [False, True])
def test_library_entrypoint_exercises_real_inventory_and_copy(live_args, monkeypatch, relocated):
    args, active, source_path = live_args
    consent = replace(args["consent"], operation_id="e" * 32)
    mountpoint = args["mountpoint"]
    managed = mountpoint / Path(consent.target_relative_path).parent
    managed.mkdir(parents=True)
    if relocated:
        (managed / "renamed.epub").write_bytes(source_path.read_bytes())

    observe = lambda **_: active["mount"]
    clock = SimpleNamespace(time=lambda: NOW)
    monkeypatch.setattr(managed_scan, "observe_linux_usb_mount", observe)
    monkeypatch.setattr(preflight, "observe_linux_usb_mount", observe)
    monkeypatch.setattr(dry_run, "scan_managed_usb", managed_scan.scan_managed_usb)
    monkeypatch.setattr(dry_run, "preflight_usb_target", preflight.preflight_usb_target)
    monkeypatch.setattr(recovery.linux_mount, "_fd_mount_id", lambda _: 52)
    monkeypatch.setattr(recovery, "_boot_id", lambda: "15d003bd-94a9-4317-a221-292d3b465fe0")
    for module in (managed_scan, dry_run, library_copy, supervisor):
        monkeypatch.setattr(module, "time", clock)

    journal = SqliteTestJournal(mountpoint.parent / "supervised.sqlite3")
    transport = SimpleNamespace(
        plugin_id="storage.usb", registry_generation=5, capabilities=(USB_STAGE_CAPABILITY,),
        service=SimpleNamespace(write_usb_stage=lambda payload, append: append(payload)),
    )
    result = library_copy.execute_usb_library_copy(
        **(args | {"consent": consent}), journal=journal, lock_root=mountpoint.parent / "locks",
        plugin_manager=SimpleNamespace(services=lambda _: (transport,)),
        plugin_id=transport.plugin_id, registry_generation=transport.registry_generation,
    )
    final = mountpoint / consent.target_relative_path
    if relocated:
        assert result.reason == "usb_copy_target_review_refused"
        assert not final.exists()
        assert journal.get(consent.operation_id) is None
        assert (managed / "renamed.epub").read_bytes() == source_path.read_bytes()
    else:
        assert result.status == "completed"
        assert final.read_bytes() == source_path.read_bytes()
        assert journal.get(consent.operation_id).receipt == result.receipt
