"""USB recovery facts after an interrupted copy, on a synthetic mount."""
from contextlib import ExitStack
from dataclasses import replace
from hashlib import sha256
import os

import pytest

from cifereca.core import device_usb_copy_recovery as recovery
from cifereca.core.device_usb_file_commit import publish_usb_staged_file
from cifereca.core.device_usb_mount import LinuxUsbMount


PAYLOAD = b"staged recovery fixture"
SHA = sha256(PAYLOAD).hexdigest()
TEMP = ".cifereca-usb-" + "e" * 32 + ".part"
FINAL = SHA + ".epub"
BOOT = "15d003bd-94a9-4317-a221-292d3b465fe0"


@pytest.fixture
def copy(tmp_path, monkeypatch):
    directory = tmp_path / "Books"
    directory.mkdir()
    stage = directory / TEMP
    stage.write_bytes(PAYLOAD)
    device = directory.stat().st_dev
    mount = LinuxUsbMount(
        "alice", "reader-a", "uuid-reader-a", tmp_path, "ext4",
        os.major(device), os.minor(device), 1800000000, 45,
    )
    monkeypatch.setattr(recovery, "_boot_id", lambda: BOOT)
    monkeypatch.setattr(recovery.linux_mount, "_fd_mount_id", lambda _: 45)
    native_open = os.open

    def open_with_synthetic_proc(path, flags, *args, **kwargs):
        if isinstance(path, str) and path.startswith("/proc/self/fd/"):
            marker = os.fstat(int(path.rsplit("/", 1)[-1]))
            for candidate in directory.iterdir():
                item = candidate.lstat()
                if (marker.st_dev, marker.st_ino) == (item.st_dev, item.st_ino):
                    return native_open(candidate, flags | os.O_NOFOLLOW)
            raise FileNotFoundError(path)
        return native_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(recovery.os, "open", open_with_synthetic_proc)
    with ExitStack() as stack:
        dir_fd = os.open(directory, os.O_DIRECTORY | os.O_RDONLY)
        staged_fd = os.open(stage, os.O_RDONLY)
        stack.callback(os.close, dir_fd)
        stack.callback(os.close, staged_fd)
        pin = recovery.capture_usb_copy_recovery_pin(
            directory_fd=dir_fd, staged_fd=staged_fd, mount=mount,
            staged_name=TEMP, target_relative_path="Books/" + FINAL,
            sha256=SHA, size=len(PAYLOAD),
        )
        assert pin is not None
        yield dict(directory_fd=dir_fd, mount=mount, pin=pin), staged_fd, directory


def test_stage_can_be_reopened_for_review_and_renamed_file_is_a_candidate(copy):
    args, fd, directory = copy
    assert recovery.inspect_usb_copy_recovery(**args).status == "stage_candidate"
    result = publish_usb_staged_file(
        directory_fd=args["directory_fd"], staged_fd=fd, mount_id=args["mount"].mount_id,
        staged_name=TEMP, target_name=FINAL, expected_sha256=SHA, size=len(PAYLOAD),
        pending_is_current=lambda: True,
    )
    assert result.status == "published"
    review = recovery.inspect_usb_copy_recovery(**args)
    assert review.status == "final_candidate"
    assert (directory / FINAL).read_bytes() == PAYLOAD
    assert not (directory / TEMP).exists()


def test_same_sha_at_final_name_does_not_replace_inode_proof(copy):
    args, fd, directory = copy
    (directory / TEMP).unlink()
    (directory / FINAL).write_bytes(PAYLOAD)
    assert recovery.inspect_usb_copy_recovery(**args).status == "conflict"
    assert (directory / FINAL).read_bytes() == PAYLOAD


@pytest.mark.parametrize("name", [FINAL, TEMP.upper()])
def test_parallel_name_or_case_alias_stays_in_conflict(copy, name):
    args, fd, directory = copy
    (directory / name).write_bytes(PAYLOAD)
    assert recovery.inspect_usb_copy_recovery(**args).status == "conflict"
    assert len(list(directory.iterdir())) == 2


def test_absent_names_are_observed_without_touching_the_volume(copy):
    args, fd, directory = copy
    (directory / TEMP).unlink()
    assert recovery.inspect_usb_copy_recovery(**args).status == "missing"
    assert list(directory.iterdir()) == []


@pytest.mark.parametrize("field,value", [
    ("mount_id", 46), ("usb_fingerprint", "uuid-other"),
    ("identity_id", "bob"), ("device_id", "another-reader"),
])
def test_other_mount_or_scope_cannot_reuse_the_pin(copy, field, value):
    args, fd, _ = copy
    args["mount"] = replace(args["mount"], **{field: value})
    assert recovery.inspect_usb_copy_recovery(**args).status == "unverified"


def test_reboot_requires_independent_reconciliation(copy, monkeypatch):
    args, fd, _ = copy
    monkeypatch.setattr(recovery, "_boot_id", lambda: "another-boot")
    assert recovery.inspect_usb_copy_recovery(**args).status == "unverified"


def test_replaced_managed_directory_rejects_old_fd(copy):
    args, fd, directory = copy
    moved = directory.with_name("Moved")
    directory.rename(moved)
    directory.mkdir()
    assert recovery.inspect_usb_copy_recovery(**args).status == "unverified"
    assert (moved / TEMP).read_bytes() == PAYLOAD


def test_journal_pin_is_persisted_before_any_final_name(copy):
    args, fd, directory = copy
    pending = []

    def checkpoint(pin):
        assert (directory / TEMP).exists()
        assert not (directory / FINAL).exists()
        pending.append(pin)
        return True

    result = recovery.publish_usb_pending_copy(
        directory_fd=args["directory_fd"], staged_fd=fd, mount=args["mount"],
        staged_name=TEMP, target_relative_path="Books/" + FINAL, sha256=SHA,
        size=len(PAYLOAD), checkpoint_pending=checkpoint,
        pending_is_current=lambda pin: pending == [pin],
    )
    assert result.status == "published"
    assert pending == [args["pin"]]
    assert recovery.inspect_usb_copy_recovery(**args).status == "final_candidate"


def test_checkpoint_failure_leaves_stage_and_blocks_rename(copy):
    args, fd, directory = copy

    def checkpoint(_pin):
        raise OSError("journal unavailable")

    result = recovery.publish_usb_pending_copy(
        directory_fd=args["directory_fd"], staged_fd=fd, mount=args["mount"],
        staged_name=TEMP, target_relative_path="Books/" + FINAL, sha256=SHA,
        size=len(PAYLOAD), checkpoint_pending=checkpoint,
        pending_is_current=lambda pin: pytest.fail("checkpoint was never committed"),
    )
    assert (result.status, result.reason, result.detail) == (
        "blocked", "usb_recovery_checkpoint_failed", None,
    )
    assert (directory / TEMP).read_bytes() == PAYLOAD
    assert not (directory / FINAL).exists()


def test_same_bytes_changed_after_checkpoint_are_not_published_under_old_pin(copy):
    args, fd, directory = copy
    pending = []

    def checkpoint(pin):
        pending.append(pin)
        info = (directory / TEMP).stat()
        os.utime(directory / TEMP, ns=(info.st_atime_ns, info.st_mtime_ns + 2_000_000_000))
        return True

    result = recovery.publish_usb_pending_copy(
        directory_fd=args["directory_fd"], staged_fd=fd, mount=args["mount"],
        staged_name=TEMP, target_relative_path="Books/" + FINAL, sha256=SHA,
        size=len(PAYLOAD), checkpoint_pending=checkpoint,
        pending_is_current=lambda pin: pending == [pin],
    )
    assert result.reason == "usb_publish_pending_not_current"
    assert (directory / TEMP).read_bytes() == PAYLOAD
    assert not (directory / FINAL).exists()
