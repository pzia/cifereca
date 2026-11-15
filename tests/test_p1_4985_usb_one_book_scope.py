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
