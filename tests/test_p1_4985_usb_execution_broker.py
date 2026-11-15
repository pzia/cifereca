"""P1 #4985: one-book USB Core broker, synthetic mounts and real SQLite."""
from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace as NS
from time import time

import pytest

from cifereca.core import device_usb_execution_broker as mod
from cifereca.core.device_usb_copy_admission import UsbCopyConsent
from cifereca.core.device_usb_dry_run import UsbDryRun
from cifereca.core.device_usb_mount import LinuxUsbMount
from cifereca.core.device_usb_preview import UsbPreviewRow
from cifereca.core.device_usb_removal_review import UsbRemovalConsent
from cifereca.core.device_usb_sync_engine import UsbSyncResult
from cifereca.identities import Identity
from cifereca.storage import Storage


@pytest.fixture
def rig(tmp_path, monkeypatch):
    db = Storage(tmp_path / "library.sqlite3")
    db.identity_put(Identity("alice", "Alice"))
    db.identity_put(Identity("bob", "Bob"))
    db.device_usb_binding_declare(
        identity_id="alice", device_id="reader-a", label="Reader",
        usb_fingerprint="UUID-ALICE-001", enabled=True,
    )
    payload = b"synthetic local book"
    digest = sha256(payload).hexdigest()
    artifact_id = "sha256:" + digest
    root = tmp_path / "alice"
    root.mkdir()
    local = root / "story.epub"
    local.write_bytes(payload)
    db.artifact_put({
        "artifact_id": artifact_id, "sha256": digest, "size": len(payload),
        "media_format": "epub", "metadata": {"artifact_kind": "file"},
    })
    db.placement_put({
        "artifact_id": artifact_id, "path": str(local),
        "identity_id": "alice", "provider_id": "local",
        "source_item_id": "fixture", "state": "present", "metadata": {},
    })
    db.device_usb_intent_set(
        identity_id="alice", device_id="reader-a",
        artifact_id=artifact_id, desired_state="keep",
    )
    target = "Books/Cifereca/" + digest + ".epub"
    db.device_usb_target_set(
        identity_id="alice", device_id="reader-a",
        artifact_id=artifact_id, target_relative_path=target,
    )
    db.device_usb_intent_set(
        identity_id="alice", device_id="reader-a",
        artifact_id=artifact_id, desired_state="remove",
    )
    mount = tmp_path / "mount"
    mount.mkdir()
    service = NS(current_identity=NS(identity_id="alice"),
                 _account_context_epoch=1, storage=db, config=object(),
                 _plugin_manager=NS(registry_generation=4))
    monkeypatch.setattr(
        mod, "configured_identity_roots",
        lambda _config, identity: {identity: (root,)},
    )
    def mount_proof(*, identity_id, binding, mountpoint):
        return LinuxUsbMount(
            identity_id, binding["device_id"],
            binding["metadata"]["usb_fingerprint"], mountpoint,
            "vfat", 8, 1, int(time()), 77,
        )
    monkeypatch.setattr(mod, "observe_linux_usb_mount", mount_proof)
    consent = UsbRemovalConsent(
        "alice", "reader-a", "UUID-ALICE-001", artifact_id, digest,
        target, "a" * 32, "b" * 32, int(time()), int(time()) + 240,
    )
    row = UsbPreviewRow(
        artifact_id, "remove", "present", "unknown", None,
        "remove_candidate", "managed_removal_requires_confirmation",
    )
    report = UsbDryRun("alice", "reader-a", "scanned", True, int(time()), (row,))
    monkeypatch.setattr(mod, "inspect_usb_dry_run", lambda **_: report)
    return db, service, mount, consent, report, local


def test_public_broker_is_trusted_only(rig):
    db, service, mount, consent, report, local = rig
    instance = mod.ReaderUsbExecutionBrokerImpl(service)
    assert instance.for_plugin("foreign.plugin", trusted=False) is None
    assert instance.for_plugin("builtin.ui", trusted=True) is instance
    from cifereca.brokers import build_public_brokers
    assert type(build_public_brokers(service)["reader.usb"]) is type(instance)


def test_confirmed_remove_delegates_exact_book_to_locked_core(rig, monkeypatch):
    db, service, mount, consent, report, local = rig
    captured = []
    def run(**kwargs):
        captured.append(kwargs)
        assert kwargs["current_profile"]() == ("alice", local.parent, 1)
        return UsbSyncResult("completed", removed=1, observed=True)
    monkeypatch.setattr(mod, "synchronize_usb_reader", run)
    original = local.read_bytes()
    result = mod.ReaderUsbExecutionBrokerImpl(service).execute_one(
        confirmation=consent, mountpoint=mount,
    )
    assert result == UsbSyncResult("completed", removed=1, observed=True)
    assert len(captured) == 1
    assert captured[0]["confirmations"] == {consent.artifact_id: consent}
    assert captured[0]["rights"] == {}
    assert captured[0]["policy_verifier"] is None
    assert captured[0]["registry_generation"] == 4
    assert captured[0]["plugin_id"] == "storage.usb"
    assert local.read_bytes() == original
    assert db.device_placement_get("reader-a", consent.artifact_id)["observed_state"] == "unknown"


def test_upload_never_derives_rights_from_file_or_user_consent(rig, monkeypatch):
    db, service, mount, c, report, local = rig
    monkeypatch.setattr(
        mod, "synchronize_usb_reader",
        lambda **_: pytest.fail("no copy rights verifier"),
    )
    upload = UsbCopyConsent(
        c.identity_id, c.device_id, c.usb_fingerprint,
        c.artifact_id, c.sha256, "epub", c.target_relative_path,
        "c" * 32, int(time()), int(time()) + 240,
    )
    assert mod.ReaderUsbExecutionBrokerImpl(service).execute_one(
        confirmation=upload, mountpoint=mount,
    ) == UsbSyncResult("blocked")


@pytest.mark.parametrize("change", [
    "foreign_profile", "bad_epoch", "disabled", "wrong_target",
    "wrong_fingerprint", "root", "missing_mount", "foreign_identity",
])
def test_stale_or_invalid_scope_refuses_to_execute(rig, monkeypatch, change):
    db, service, mount, c, report, local = rig
    if change == "foreign_profile":
        service.current_identity = NS(identity_id="bob")
    elif change == "bad_epoch":
        service._account_context_epoch = "1"
    elif change == "disabled":
        db.device_usb_binding_declare(
            identity_id="alice", device_id="reader-a", label="Reader",
            usb_fingerprint="UUID-ALICE-001", enabled=False,
        )
    elif change == "wrong_target":
        db.device_placement_put({
            "device_id": "reader-a", "artifact_id": c.artifact_id,
            "desired_state": "remove", "observed_state": "unknown",
            "target_relative_path": "Books/Cifereca/changed.epub",
            "metadata": {"intent_identity_id": "alice"},
        })
    elif change == "wrong_fingerprint":
        c = replace(c, usb_fingerprint="UUID-FOREIGN")
    elif change == "root":
        mount = Path("/")
    elif change == "missing_mount":
        monkeypatch.setattr(mod, "observe_linux_usb_mount", lambda **_: None)
    else:
        c = replace(c, identity_id="bob")
    monkeypatch.setattr(
        mod, "synchronize_usb_reader",
        lambda **_: pytest.fail("unexpected execution"),
    )
    assert mod.ReaderUsbExecutionBrokerImpl(service).execute_one(
        confirmation=c, mountpoint=mount,
    ) == UsbSyncResult("blocked")


@pytest.mark.parametrize("action", ["verify", "upload_candidate", "skip", "blocked"])
def test_no_managed_removal_candidate_never_calls_executor(rig, monkeypatch, action):
    _, service, mount, c, report, _ = rig
    monkeypatch.setattr(
        mod, "inspect_usb_dry_run",
        lambda **_: replace(report, rows=(replace(report.rows[0], action=action),)),
    )
    monkeypatch.setattr(
        mod, "synchronize_usb_reader",
        lambda **_: pytest.fail("not a managed remove"),
    )
    assert mod.ReaderUsbExecutionBrokerImpl(service).execute_one(
        confirmation=c, mountpoint=mount,
    ) == UsbSyncResult("unavailable")


def test_aba_during_scan_revokes_execution(rig, monkeypatch):
    _, service, mount, c, report, _ = rig
    def stale(**_):
        service._account_context_epoch += 2
        return report
    monkeypatch.setattr(mod, "inspect_usb_dry_run", stale)
    monkeypatch.setattr(mod, "synchronize_usb_reader",
                        lambda **_: pytest.fail("ABA not revoked"))
    assert mod.ReaderUsbExecutionBrokerImpl(service).execute_one(
        confirmation=c, mountpoint=mount,
    ) == UsbSyncResult("unavailable")


def test_read_only_preview_returns_only_scoped_fresh_report(rig, monkeypatch):
    _, service, mount, c, report, local = rig
    instance = mod.ReaderUsbExecutionBrokerImpl(service)
    before = local.read_bytes()
    assert instance.preview(identity_id="alice", device_id="reader-a", mountpoint=mount) == report
    service._account_context_epoch += 1
    assert instance.preview(identity_id="bob", device_id="reader-a", mountpoint=mount).status == "unavailable"
    assert local.read_bytes() == before


def test_preview_discards_post_scan_profile_change(rig, monkeypatch):
    _, service, mount, c, report, local = rig
    def stale(**_):
        service.current_identity = NS(identity_id="bob")
        return report
    monkeypatch.setattr(mod, "inspect_usb_dry_run", stale)
    assert mod.ReaderUsbExecutionBrokerImpl(service).preview(
        identity_id="alice", device_id="reader-a", mountpoint=mount,
    ).status == "unavailable"
