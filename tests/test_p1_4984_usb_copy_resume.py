"""#4984: restart a USB copy after crash before/after atomic rename."""
from dataclasses import replace

import pytest

from cifereca.core import device_usb_copy_recovery as recovery
from cifereca.core import device_usb_copy_supervisor as supervisor
from cifereca.core.device_usb_copy_resume import reconcile_usb_pending_copy
from tests.test_p1_2854_usb_copy_supervisor import copy


def _resume(args, **overrides):
    kwargs = dict(
        journal=args["journal"], lock_root=args["lock_root"],
        mount=args["mount"], directory_fd=args["directory_fd"],
        operation_id=args["consent"].operation_id,
        plugin_manager=args["plugin_manager"], plugin_id=args["plugin_id"],
        registry_generation=args["registry_generation"],
        revalidate=args["revalidate"],
    )
    return reconcile_usb_pending_copy(**(kwargs | overrides))


def _crash_after_checkpoint(args, monkeypatch, *, after_rename):
    original = supervisor.publish_usb_pending_copy

    def cut_power(**kwargs):
        if after_rename:
            published = original(**kwargs)
            assert published.status == "published"
        else:
            pin = recovery.capture_usb_copy_recovery_pin(**{
                key: kwargs[key] for key in (
                    "directory_fd", "staged_fd", "mount", "staged_name",
                    "target_relative_path", "sha256", "size",
                )
            })
            assert pin is not None
            assert kwargs["checkpoint_pending"](pin)
        raise OSError("synthetic power cut")

    monkeypatch.setattr(supervisor, "publish_usb_pending_copy", cut_power)
    first = supervisor.execute_usb_copy(**args)
    assert first.status == "recovery_required"
    checkpoint = args["journal"].get(args["consent"].operation_id)
    assert checkpoint.phase == "checkpointed" and checkpoint.pin is not None
    return checkpoint


@pytest.mark.parametrize("after_rename", [False, True])
def test_checkpointed_copy_resumes_without_new_consent(
    copy, monkeypatch, after_rename,
):
    args, managed, _, _ = copy
    checkpoint = _crash_after_checkpoint(args, monkeypatch, after_rename=after_rename)
    path = managed / (args["consent"].sha256 + ".epub")
    assert path.exists() is after_rename
    result = _resume(args)
    assert result.status == "completed", result.reason
    assert result.receipt is not None
    assert path.read_bytes() == args["payload"]
    finished = args["journal"].get(args["consent"].operation_id)
    assert finished.phase == "completed"
    assert finished.pin == checkpoint.pin
    assert finished.receipt == result.receipt
    assert finished.final_version is not None
    second = _resume(args)
    assert second.status == "blocked"
    assert path.read_bytes() == args["payload"]


def test_reconnect_or_rights_revocation_does_not_adopt_file(copy, monkeypatch):
    args, managed, _, _ = copy
    _crash_after_checkpoint(args, monkeypatch, after_rename=True)
    final = managed / (args["consent"].sha256 + ".epub")
    changed = _resume(args, mount=replace(args["mount"], mount_id=999))
    assert changed.status == "blocked"
    revoked = _resume(args, revalidate=lambda *_: False)
    assert revoked.status == "blocked"
    assert args["journal"].get(args["consent"].operation_id).phase == "checkpointed"
    assert final.read_bytes() == args["payload"]


def test_changed_pending_stage_requires_review_not_overwrite(copy, monkeypatch):
    args, managed, _, _ = copy
    checkpoint = _crash_after_checkpoint(args, monkeypatch, after_rename=False)
    stage = managed / checkpoint.pin.staged_name
    stage.write_bytes(b"tampered")
    result = _resume(args)
    assert result.status == "recovery_required"
    assert args["journal"].get(args["consent"].operation_id).phase == "checkpointed"
    assert not (managed / (args["consent"].sha256 + ".epub")).exists()
