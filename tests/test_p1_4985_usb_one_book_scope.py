"""#4985: one-book USB confirmation cannot inherit other reader wishes."""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from cifereca.core import device_usb_sync_engine as engine
from cifereca.core.device_usb_dry_run import UsbDryRun
from cifereca.core.device_usb_copy_supervisor import UsbCopyExecution
from tests.test_p1_4984_usb_sync_engine import _arguments, _row
from tests.test_p1_2854_usb_copy_supervisor import copy


def test_one_artifact_dispatches_once_without_foreign_pending_counts(
    copy, tmp_path, monkeypatch,
):
    args, _, _, _ = copy
    selected = args["consent"].artifact_id
    data = _arguments(tmp_path, args["consent"], args["rights"])
    data["artifact_id"] = selected
    seen = []
    def inspect(**kw):
        assert kw["artifact_id"] == selected
        seen.append(("scan", selected))
        # An exact selected-book scan must return only this book. A separate
        # unconfirmed wish on the same reader is intentionally not counted.
        return UsbDryRun(
            data["identity_id"], data["device_id"],
            "scanned", True, 1, (_row(selected, "upload_candidate"),),
        )
    monkeypatch.setattr(engine, "inspect_usb_dry_run", inspect)
    monkeypatch.setattr(engine, "SqliteUsbOperationJournal", lambda _: object())
    monkeypatch.setattr(
        engine, "execute_usb_library_copy",
        lambda **kw: seen.append(("copy", kw["consent"].artifact_id))
        or UsbCopyExecution("completed", "committed"),
    )
    monkeypatch.setattr(
        engine, "execute_usb_removal",
        lambda **_: pytest.fail("another book must not be removed"),
    )
    monkeypatch.setattr(
        engine, "record_verified_usb_observation",
        lambda **_: NS(status="recorded"),
    )
    result = engine.synchronize_usb_reader(**data)
    assert result == engine.UsbSyncResult(
        "completed", copied=1, observed=True,
    )
    assert seen == [("scan", selected), ("copy", selected)]


def test_broad_reader_convergence_preserves_original_unscoped_delta(
    copy, tmp_path, monkeypatch,
):
    args, _, _, _ = copy
    selected = args["consent"].artifact_id
    other = "sha256:" + "b" * 64
    data = _arguments(tmp_path, args["consent"], args["rights"])
    def inspect(**kw):
        assert kw["artifact_id"] is None
        return UsbDryRun(
            data["identity_id"], data["device_id"], "scanned", True, 1,
            (_row(selected, "noop"), _row(other, "upload_candidate")),
        )
    monkeypatch.setattr(engine, "inspect_usb_dry_run", inspect)
    monkeypatch.setattr(engine, "SqliteUsbOperationJournal", lambda _: object())
    monkeypatch.setattr(
        engine, "execute_usb_library_copy",
        lambda **_: pytest.fail("foreign unconfirmed copy"),
    )
    monkeypatch.setattr(
        engine, "record_verified_usb_observation",
        lambda **_: NS(status="recorded"),
    )
    result = engine.synchronize_usb_reader(**data)
    assert result == engine.UsbSyncResult(
        "pending", pending=1, observed=True,
    )


def test_one_book_preview_with_multiple_rows_is_refused(
    copy, tmp_path, monkeypatch,
):
    args, _, _, _ = copy
    selected = args["consent"].artifact_id
    other = "sha256:" + "b" * 64
    data = _arguments(tmp_path, args["consent"], args["rights"])
    data["artifact_id"] = selected
    monkeypatch.setattr(
        engine, "inspect_usb_dry_run",
        lambda **_: UsbDryRun(
            data["identity_id"], data["device_id"], "scanned", True, 1,
            (_row(selected, "upload_candidate"),
             _row(other, "upload_candidate")),
        ),
    )
    monkeypatch.setattr(
        engine, "SqliteUsbOperationJournal",
        lambda _: pytest.fail("unscoped preview cannot reach journal"),
    )
    assert engine.synchronize_usb_reader(**data) == engine.UsbSyncResult("unavailable")


@pytest.mark.parametrize("invalid", [
    "", "sha256:bad", "sha256:" + "A" * 64, 17, True, [], {},
])
def test_invalid_single_book_scope_rejected_before_usb_scan(
    copy, tmp_path, monkeypatch, invalid,
):
    args, _, _, _ = copy
    data = _arguments(tmp_path, args["consent"], args["rights"])
    data["artifact_id"] = invalid
    monkeypatch.setattr(
        engine, "inspect_usb_dry_run",
        lambda **_: pytest.fail("invalid scope must never scan USB"),
    )
    assert engine.synchronize_usb_reader(**data) == engine.UsbSyncResult("blocked")


def test_malformed_one_book_preview_row_is_not_executable(
    copy, tmp_path, monkeypatch,
):
    args, _, _, _ = copy
    data = _arguments(tmp_path, args["consent"], args["rights"])
    data["artifact_id"] = args["consent"].artifact_id
    monkeypatch.setattr(
        engine, "inspect_usb_dry_run",
        lambda **_: UsbDryRun(
            data["identity_id"], data["device_id"], "scanned", True,
            1, ({"artifact_id": data["artifact_id"]},),
        ),
    )
    monkeypatch.setattr(
        engine, "SqliteUsbOperationJournal",
        lambda _: pytest.fail("malformed Core preview must not reach journal"),
    )
    assert engine.synchronize_usb_reader(**data) == engine.UsbSyncResult("unavailable")


@pytest.mark.parametrize("single", [True, False])
def test_confirmed_removal_count_ignores_other_pending_upload_only_if_scoped(
    copy, tmp_path, monkeypatch, single,
):
    """Exact regression: unrelated wishes cannot mask a completed unlink."""
    from cifereca.core.device_usb_removal_review import UsbRemovalConsent
    from cifereca.core.device_usb_removal_execute import UsbRemovalExecution

    args, _, _, _ = copy
    original = args["consent"]
    removal = UsbRemovalConsent(
        original.identity_id, original.device_id,
        original.usb_fingerprint, original.artifact_id,
        original.sha256, original.target_relative_path,
        "b" * 32, "c" * 32, original.confirmed_at, original.expires_at,
    )
    data = _arguments(tmp_path, removal, None)
    data["confirmations"] = {removal.artifact_id: removal}
    data["rights"] = {}
    if single:
        data["artifact_id"] = removal.artifact_id
    other = "sha256:" + "e" * 64
    scans, removals = [], []

    def inspect(**kwargs):
        scans.append(kwargs["artifact_id"])
        rows = (_row(removal.artifact_id, "remove_candidate"),)
        if not single:
            rows += (_row(other, "upload_candidate"),)
        return UsbDryRun(
            removal.identity_id, removal.device_id, "scanned", True, 1, rows,
        )

    monkeypatch.setattr(engine, "inspect_usb_dry_run", inspect)
    monkeypatch.setattr(engine, "SqliteUsbOperationJournal", lambda _: object())
    monkeypatch.setattr(
        engine, "execute_usb_removal",
        lambda **kwargs: removals.append(kwargs["consent"])
        or UsbRemovalExecution("removed", "committed"),
    )
    monkeypatch.setattr(
        engine, "execute_usb_library_copy",
        lambda **_: pytest.fail("unconfirmed other upload must not run"),
    )
    monkeypatch.setattr(
        engine, "record_verified_usb_observation",
        lambda **_: NS(status="recorded"),
    )
    result = engine.synchronize_usb_reader(**data)
    assert scans == [removal.artifact_id if single else None]
    assert removals == [removal]
    assert result == engine.UsbSyncResult(
        "completed" if single else "pending",
        removed=1, pending=0 if single else 1, observed=True,
    )


def test_revoked_selection_after_removal_refusal_skips_durable_observation(
    copy, tmp_path, monkeypatch,
):
    """An inode-review cancellation cannot persist a later USB presence."""
    from cifereca.core.device_usb_removal_review import UsbRemovalConsent
    from cifereca.core.device_usb_removal_execute import UsbRemovalExecution

    args, _, _, _ = copy
    original = args["consent"]
    consent = UsbRemovalConsent(
        original.identity_id, original.device_id,
        original.usb_fingerprint, original.artifact_id,
        original.sha256, original.target_relative_path,
        "b" * 32, "c" * 32, original.confirmed_at, original.expires_at,
    )
    data = _arguments(tmp_path, consent, None)
    data["rights"] = {}
    data["artifact_id"] = consent.artifact_id
    state = {"revoked": False, "checks": 0}

    def check_selected():
        state["checks"] += 1
        if state["revoked"]:
            raise RuntimeError("reader_selection_revoked")

    def cancelled_remove(**kwargs):
        assert kwargs["check_cancelled"] is check_selected
        state["revoked"] = True
        return UsbRemovalExecution("blocked", "usb_removal_scope_changed")

    data["check_cancelled"] = check_selected
    monkeypatch.setattr(
        engine, "inspect_usb_dry_run", lambda **_: UsbDryRun(
            consent.identity_id, consent.device_id, "scanned", True, 1,
            (_row(consent.artifact_id, "remove_candidate"),),
        ),
    )
    monkeypatch.setattr(engine, "SqliteUsbOperationJournal", lambda _: object())
    monkeypatch.setattr(engine, "execute_usb_removal", cancelled_remove)
    monkeypatch.setattr(
        engine, "record_verified_usb_observation",
        lambda **_: pytest.fail("cancelled UI must never persist presence"),
    )
    # The batch now returns a closed receipt even when revocation follows
    # a failed operation. This must not permit a durable observation.
    result = engine.synchronize_usb_reader(**data)
    assert result == engine.UsbSyncResult("blocked", failed=1, observed=False)
    assert state == {"revoked": True, "checks": 2}
