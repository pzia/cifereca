"""#4984: one convergence engine serves independent identities and USB readers."""
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

from cifereca.core import device_usb_sync_engine as sync
from cifereca.core.device_usb_dry_run import UsbDryRun
from cifereca.core.device_usb_preview import UsbPreviewRow
from cifereca.core.device_usb_copy_supervisor import UsbCopyExecution
from tests.test_p1_2854_usb_copy_supervisor import copy


def test_two_identities_and_devices_with_same_sha_remain_distinct(
    copy, tmp_path, monkeypatch,
):
    args, _, _, _ = copy
    original_consent, original_rights = args["consent"], args["rights"]
    second_consent = replace(
        original_consent, identity_id="bob", device_id="reader-b",
        usb_fingerprint="uuid-reader-b", operation_id="b" * 32,
    )
    second_rights = replace(
        original_rights, identity_id="bob", device_id="reader-b",
        usb_fingerprint="uuid-reader-b", account_id="bob-account",
    )
    assert original_consent.sha256 == second_consent.sha256
    expected = {
        (original_consent.identity_id, original_consent.device_id):
            (original_consent, original_rights),
        (second_consent.identity_id, second_consent.device_id):
            (second_consent, second_rights),
    }
    changes = {key: "upload_candidate" for key in expected}
    actual_writes = []

    def preview(*, identity_id, device_id, **_):
        consent, _rights = expected[identity_id, device_id]
        action = changes[identity_id, device_id]
        return UsbDryRun(
            identity_id, device_id, "scanned", True, 1800000000,
            (UsbPreviewRow(
                consent.artifact_id, "keep",
                "missing" if action == "upload_candidate" else "present",
                "unknown", None, action, "synthetic fixture",
            ),),
        )

    def copy_action(**values):
        consent, rights = values["consent"], values["rights"]
        assert (consent, rights) == expected[consent.identity_id, consent.device_id]
        actual_writes.append((consent.identity_id, consent.device_id, consent.operation_id))
        return UsbCopyExecution("completed", "usb_copy_receipt_committed")

    monkeypatch.setattr(sync, "inspect_usb_dry_run", preview)
    monkeypatch.setattr(sync, "execute_usb_library_copy", copy_action)
    monkeypatch.setattr(sync, "SqliteUsbOperationJournal", lambda _: object())
    monkeypatch.setattr(
        sync, "record_verified_usb_observation",
        lambda **_: SimpleNamespace(status="recorded"),
    )

    def run(consent, rights):
        class Storage:
            def device_placements(self, *, device_id):
                assert device_id == consent.device_id
                return [{
                    "artifact_id": consent.artifact_id, "placement_id": "owned-placement",
                }]
        return sync.synchronize_usb_reader(
            storage=Storage(), identity_id=consent.identity_id,
            device_id=consent.device_id, identity_root=tmp_path / consent.identity_id,
            mountpoint=tmp_path / consent.device_id, lock_root=tmp_path / "locks",
            current_profile=lambda: (consent.identity_id, tmp_path / consent.identity_id, 1),
            plugin_manager=object(), plugin_id="storage.usb",
            registry_generation=1,
            confirmations={consent.artifact_id: consent},
            rights={consent.artifact_id: rights}, policy_verifier=lambda _: True,
        )

    for consent, rights in expected.values():
        result = run(consent, rights)
        assert (result.status, result.copied, result.removed, result.observed) == (
            "completed", 1, 0, True,
        )
    assert len(actual_writes) == 2
    assert len(set(actual_writes)) == 2

    # Subsequent attested scans show each reader already up-to-date.
    for key in expected:
        changes[key] = "noop"
    for consent, rights in expected.values():
        result = run(consent, rights)
        assert result.status == "completed"
        assert result.copied == 0
    assert len(actual_writes) == 2


def test_unavailable_second_reader_does_not_block_first(
    copy, tmp_path, monkeypatch,
):
    args, _, _, _ = copy
    consent = args["consent"]
    calls = []
    monkeypatch.setattr(sync, "inspect_usb_dry_run", lambda **_: UsbDryRun(
        consent.identity_id, consent.device_id, "mount_unverified",
        False, None, (),
    ))
    monkeypatch.setattr(sync, "execute_usb_library_copy",
                        lambda **_: calls.append("unexpected"))
    result = sync.synchronize_usb_reader(
        storage=object(), identity_id=consent.identity_id,
        device_id=consent.device_id,
        identity_root=tmp_path / "profile",
        mountpoint=tmp_path / "absent",
        lock_root=tmp_path / "locks",
        current_profile=lambda: ("alice", tmp_path / "profile", 1),
        plugin_manager=object(), plugin_id="storage.usb",
        registry_generation=1,
        confirmations={consent.artifact_id: consent},
        rights={consent.artifact_id: args["rights"]},
        policy_verifier=lambda _: True,
    )
    assert result.status == "unavailable"
    assert result.observed is False
    assert calls == []
