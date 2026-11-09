"""P1 #4984: one Core convergence path, no invented rights from a delta."""
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from cifereca.core import device_usb_sync_engine as engine
from cifereca.core.device_usb_dry_run import UsbDryRun
from cifereca.core.device_usb_preview import UsbPreviewRow
from cifereca.core.device_usb_copy_supervisor import UsbCopyExecution
from cifereca.core.device_usb_removal_execute import UsbRemovalExecution
from cifereca.core.device_usb_removal_review import UsbRemovalConsent
from tests.test_p1_2854_usb_copy_supervisor import copy


def _row(artifact, action):
    return UsbPreviewRow(
        artifact_id=artifact, desired="remove" if action == "remove_candidate" else "keep",
        observed="present" if action == "remove_candidate" else "missing",
        last_known="unknown", last_observed_at=None,
        action=action, reason="synthetic",
    )


def _arguments(tmp_path, consent, rights):
    class Storage:
        def device_placements(self, *, device_id):
            assert device_id == consent.device_id
            return [{"artifact_id": consent.artifact_id, "placement_id": "local-copy"}]

    return dict(
        storage=Storage(), identity_id=consent.identity_id, device_id=consent.device_id,
        identity_root=tmp_path / "identity", mountpoint=tmp_path / "usb",
        lock_root=tmp_path / "locks",
        current_profile=lambda: (consent.identity_id, tmp_path / "identity", 3),
        plugin_manager=object(), plugin_id="storage.usb", registry_generation=4,
        confirmations={consent.artifact_id: consent},
        rights={consent.artifact_id: rights}, policy_verifier=lambda _: True,
    )


def test_no_confirmation_preserves_pending_intention(copy, tmp_path, monkeypatch):
    args, _, _, _ = copy
    consent = args["consent"]
    call = _arguments(tmp_path, consent, args["rights"])
    call["confirmations"] = {}
    monkeypatch.setattr(engine, "inspect_usb_dry_run", lambda **_: UsbDryRun(
        consent.identity_id, consent.device_id, "scanned", True, 1,
        (_row(consent.artifact_id, "upload_candidate"),),
    ))
    monkeypatch.setattr(engine, "SqliteUsbOperationJournal", lambda _: object())
    monkeypatch.setattr(engine, "execute_usb_library_copy",
                        lambda **_: pytest.fail("transfer without confirmation"))
    monkeypatch.setattr(engine, "record_verified_usb_observation",
                        lambda **_: SimpleNamespace(status="recorded"))
    result = engine.synchronize_usb_reader(**call)
    assert result.status == "pending"
    assert result.pending == 1 and result.copied == 0
    assert result.observed is True


def test_same_core_run_dispatches_copy_then_remove_with_distinct_confirmation(
    copy, tmp_path, monkeypatch,
):
    args, _, _, _ = copy
    consent = args["consent"]
    removal_sha = "b" * 64
    removal_id = "sha256:" + removal_sha
    removal = UsbRemovalConsent(
        consent.identity_id, consent.device_id, consent.usb_fingerprint,
        removal_id, removal_sha, "Books/" + removal_sha + ".epub",
        "b" * 32, "c" * 32, consent.confirmed_at, consent.expires_at,
    )
    call = _arguments(tmp_path, consent, args["rights"])
    call["confirmations"][removal_id] = removal
    monkeypatch.setattr(engine, "inspect_usb_dry_run", lambda **_: UsbDryRun(
        consent.identity_id, consent.device_id, "scanned", True, 1,
        (_row(consent.artifact_id, "upload_candidate"),
         _row(removal_id, "remove_candidate")),
    ))
    journal = object()
    monkeypatch.setattr(engine, "SqliteUsbOperationJournal", lambda _: journal)
    calls = []
    def copy_action(**kw):
        calls.append(("copy", kw["consent"], kw["journal"]))
        return UsbCopyExecution("completed", "committed")
    def removal_action(**kw):
        calls.append(("remove", kw["consent"], kw["copy_journal"]))
        return UsbRemovalExecution("removed", "committed")
    monkeypatch.setattr(engine, "execute_usb_library_copy", copy_action)
    monkeypatch.setattr(engine, "execute_usb_removal", removal_action)
    monkeypatch.setattr(engine, "record_verified_usb_observation",
                        lambda **_: SimpleNamespace(status="recorded"))
    result = engine.synchronize_usb_reader(**call)
    assert result.status == "completed"
    assert (result.copied, result.removed, result.observed) == (1, 1, True)
    assert calls == [("copy", consent, journal), ("remove", removal, journal)]


def test_absent_usb_stays_unavailable_without_touching_plugin(
    copy, tmp_path, monkeypatch,
):
    args, _, _, _ = copy
    consent = args["consent"]
    call = _arguments(tmp_path, consent, args["rights"])
    monkeypatch.setattr(engine, "inspect_usb_dry_run", lambda **_: UsbDryRun(
        consent.identity_id, consent.device_id, "mount_unverified", False,
        None, (),
    ))
    monkeypatch.setattr(engine, "SqliteUsbOperationJournal",
                        lambda _: pytest.fail("unattested volume opened journal"))
    result = engine.synchronize_usb_reader(**call)
    assert result.status == "unavailable"
    assert result.copied == result.removed == 0


def test_noop_still_persists_verified_observation(copy, tmp_path, monkeypatch):
    args, _, _, _ = copy
    consent = args["consent"]
    call = _arguments(tmp_path, consent, args["rights"])
    monkeypatch.setattr(engine, "inspect_usb_dry_run", lambda **_: UsbDryRun(
        consent.identity_id, consent.device_id, "scanned", True, 1,
        (_row(consent.artifact_id, "noop"),),
    ))
    monkeypatch.setattr(engine, "SqliteUsbOperationJournal", lambda _: object())
    monkeypatch.setattr(engine, "record_verified_usb_observation",
                        lambda **_: SimpleNamespace(status="recorded"))
    assert engine.synchronize_usb_reader(**call).observed is True
