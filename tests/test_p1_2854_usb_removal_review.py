"""Read-only USB withdrawal review over a copy produced by the real supervisor."""
from dataclasses import replace
import os
from types import SimpleNamespace

import pytest

from cifereca.core import device_usb_copy_recovery as recovery
from cifereca.core import device_usb_copy_supervisor as supervisor
from cifereca.core import device_usb_removal_review as removal
from cifereca.plugin_api import USB_STAGE_CAPABILITY
from tests.test_p1_2854_usb_copy_preparation import copy_review
from tests.test_p1_2854_usb_copy_provenance import NOW
from tests.test_p1_2854_usb_copy_supervisor import SqliteTestJournal
from tests.test_p1_2854_usb_live_source import live_args


@pytest.fixture
def completed_copy(live_args, monkeypatch):
    args, active, source = live_args
    consent = replace(args["consent"], operation_id="a" * 32)
    final = args["mountpoint"] / consent.target_relative_path
    final.parent.mkdir(parents=True)
    clock = SimpleNamespace(time=lambda: NOW)
    monkeypatch.setattr(supervisor, "time", clock)
    monkeypatch.setattr(removal, "time", clock)
    monkeypatch.setattr(removal, "observe_linux_usb_mount", lambda **_: active["mount"])
    monkeypatch.setattr(recovery, "_boot_id", lambda: "15d003bd-94a9-4317-a221-292d3b465fe0")
    monkeypatch.setattr(recovery.linux_mount, "_fd_mount_id", lambda _: 52)
    native_open = os.open

    def open_with_synthetic_proc(path, flags, *args, **kwargs):
        if isinstance(path, str) and path.startswith("/proc/self/fd/"):
            marker = os.fstat(int(path.rsplit("/", 1)[-1]))
            for candidate in final.parent.iterdir():
                item = candidate.lstat()
                if (marker.st_dev, marker.st_ino) == (item.st_dev, item.st_ino):
                    return native_open(candidate, flags | os.O_NOFOLLOW)
            raise FileNotFoundError(path)
        return native_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(recovery.os, "open", open_with_synthetic_proc)
    journal = SqliteTestJournal(args["mountpoint"].parent / "supervised.sqlite3")
    transport = SimpleNamespace(
        plugin_id="storage.usb", registry_generation=5, capabilities=(USB_STAGE_CAPABILITY,),
        service=SimpleNamespace(write_usb_stage=lambda payload, append: append(payload)),
    )
    fd = os.open(final.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        result = supervisor.execute_usb_copy(
            journal=journal, lock_root=args["mountpoint"].parent / "locks",
            mount=active["mount"], directory_fd=fd, consent=consent, rights=args["rights"],
            payload=source.read_bytes(), plugin_manager=SimpleNamespace(services=lambda _: (transport,)),
            plugin_id=transport.plugin_id, registry_generation=transport.registry_generation,
            revalidate=lambda *_: True, review_target=lambda: True,
        )
    finally:
        os.close(fd)
    assert result.status == "completed"
    args["storage"].device_usb_intent_set(
        identity_id=consent.identity_id, device_id=consent.device_id,
        artifact_id=consent.artifact_id, desired_state="remove",
    )
    confirmation = removal.UsbRemovalConsent(
        consent.identity_id, consent.device_id, consent.usb_fingerprint,
        consent.artifact_id, consent.sha256, consent.target_relative_path,
        consent.operation_id, "b" * 32, NOW - 1, NOW + 60,
    )
    return dict(
        storage=args["storage"], journal=journal, mountpoint=args["mountpoint"],
        consent=confirmation,
        current_profile=lambda: (active["profile"][0], active["profile"][2]),
    ), active, final, source


def test_removal_review_preserves_file_source_and_journal(completed_copy):
    args, _, final, source = completed_copy
    before = args["journal"].get(args["consent"].copy_operation_id)
    observed = args["storage"].device_placements()
    result = removal.review_usb_removal(**args)
    assert result == removal.UsbRemovalReview("review", "usb_removal_receipt_and_copy_verified")
    assert result.executable is False
    assert final.read_bytes() == source.read_bytes()
    assert args["journal"].get(args["consent"].copy_operation_id) == before
    assert args["journal"].get(args["consent"].operation_id) is None
    assert args["storage"].device_placements() == observed


def test_same_sha_at_replaced_target_refuses_removal(completed_copy):
    args, _, final, source = completed_copy
    final.rename(final.with_suffix(".saved"))
    final.write_bytes(source.read_bytes())
    assert removal.review_usb_removal(**args).reason == "usb_recovery_file_unattributed"
    assert final.read_bytes() == source.read_bytes()


def test_modified_ctime_on_same_inode_refuses_removal(completed_copy):
    args, _, final, _ = completed_copy
    os.chmod(final, 0o640)
    assert removal.review_usb_removal(**args).reason == "usb_recovery_file_unattributed"
    assert final.exists()


def test_copy_receipt_without_final_identity_is_insufficient(completed_copy):
    args, _, final, _ = completed_copy
    journal = args["journal"]
    original = journal.get(args["consent"].copy_operation_id)
    assert journal.compare_and_set(original, replace(original, final_version=None))
    assert removal.review_usb_removal(**args).reason == "usb_removal_receipt_unverified"
    assert final.exists()


def test_confirmation_cannot_reuse_copy_operation_or_expire(completed_copy):
    args, _, final, _ = completed_copy
    consent = args["consent"]
    for stale in (
        replace(consent, operation_id=consent.copy_operation_id),
        replace(consent, expires_at=NOW),
    ):
        result = removal.review_usb_removal(**(args | {"consent": stale}))
        assert result.reason == "usb_removal_confirmation_invalid"
    assert final.exists()


def test_reconnected_volume_requires_new_attribution(completed_copy):
    args, active, final, _ = completed_copy
    active["mount"] = replace(active["mount"], mount_id=53)
    result = removal.review_usb_removal(**args)
    assert result.status == "blocked"
    assert final.exists()


def test_missing_target_is_observed_without_any_removal(completed_copy):
    args, _, final, _ = completed_copy
    moved = final.with_suffix(".moved")
    final.rename(moved)
    assert removal.review_usb_removal(**args).status == "absent"
    assert moved.exists()


def test_wrong_intention_never_checks_mount(completed_copy, monkeypatch):
    args, _, final, _ = completed_copy
    consent = args["consent"]
    args["storage"].device_usb_intent_set(
        identity_id=consent.identity_id, device_id=consent.device_id,
        artifact_id=consent.artifact_id, desired_state="keep",
    )
    monkeypatch.setattr(removal, "observe_linux_usb_mount",
                        lambda **_: pytest.fail("wrong intent reached mount"))
    assert removal.review_usb_removal(**args).reason == "usb_removal_scope_unverified"
    assert final.exists()


def test_profile_generation_changed_during_scan_revokes_review(completed_copy, monkeypatch):
    args, active, final, _ = completed_copy
    inspect = removal.inspect_usb_copy_recovery

    def inspect_then_switch(**kwargs):
        observed = inspect(**kwargs)
        active["profile"] = (*active["profile"][:2], active["profile"][2] + 1)
        return observed

    monkeypatch.setattr(removal, "inspect_usb_copy_recovery", inspect_then_switch)
    assert removal.review_usb_removal(**args).reason == "usb_removal_scope_changed"
    assert final.exists()
