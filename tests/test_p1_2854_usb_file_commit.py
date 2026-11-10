"""Native USB publication protocol on a synthetic volume, without provider I/O."""
from contextlib import ExitStack
import errno
from hashlib import sha256
import os

import pytest

from cifereca.core import device_usb_file_commit as commit


PAYLOAD = b"synthetic staged book bytes"
DIGEST = sha256(PAYLOAD).hexdigest()
STAGED = ".cifereca-usb-" + "a" * 32 + ".part"
TARGET = DIGEST + ".epub"


@pytest.fixture
def stage(tmp_path, monkeypatch):
    stage_path = tmp_path / STAGED
    stage_path.write_bytes(PAYLOAD)
    # Only USB mount proof is synthetic. Open, pread, fsync and rename are
    # native operations on the test filesystem.
    monkeypatch.setattr(commit.linux_mount, "_fd_mount_id", lambda _fd: 42)
    with ExitStack() as stack:
        directory_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
        stack.callback(os.close, directory_fd)
        stage_fd = os.open(stage_path, os.O_RDONLY | os.O_NOFOLLOW)
        stack.callback(os.close, stage_fd)
        yield dict(
            directory_fd=directory_fd, staged_fd=stage_fd, mount_id=42,
            staged_name=STAGED, target_name=TARGET, expected_sha256=DIGEST,
            size=len(PAYLOAD), pending_is_current=lambda: True,
        ), stage_path, tmp_path / TARGET


def test_native_no_replace_commit_verifies_bytes_and_preserves_callers_fds(stage):
    args, temporary, final = stage
    calls = []

    def current():
        calls.append((temporary.exists(), final.exists()))
        return True

    result = commit.publish_usb_staged_file(**(args | {"pending_is_current": current}))
    assert result == commit.UsbFilePublication("published", "usb_publish_file_verified")
    assert calls == [(True, False), (False, True)]
    assert not temporary.exists()
    assert final.read_bytes() == PAYLOAD
    assert os.fstat(args["staged_fd"]).st_ino == final.stat().st_ino
    assert os.fstat(args["directory_fd"]).st_ino == final.parent.stat().st_ino


@pytest.mark.parametrize("name", [TARGET, TARGET.upper()])
def test_existing_target_or_casefold_alias_is_never_replaced(stage, name):
    args, temporary, final = stage
    foreign = final.with_name(name)
    foreign.write_bytes(PAYLOAD)  # identical bytes still confer no provenance
    result = commit.publish_usb_staged_file(**args)
    assert result.reason == "usb_publish_target_occupied"
    assert foreign.read_bytes() == PAYLOAD
    assert temporary.read_bytes() == PAYLOAD


def test_native_rename_refuses_collision_created_after_userspace_check(stage, monkeypatch):
    args, temporary, final = stage
    native = commit._rename_exclusive

    def race(*rename_args):
        final.write_bytes(b"late foreign file")
        native(*rename_args)

    monkeypatch.setattr(commit, "_rename_exclusive", race)
    result = commit.publish_usb_staged_file(**args)
    assert (result.status, result.reason, result.errno) == (
        "blocked", "usb_publish_target_occupied", errno.EEXIST,
    )
    assert final.read_bytes() == b"late foreign file"
    assert temporary.read_bytes() == PAYLOAD


@pytest.mark.parametrize("change", ["different_bytes", "replaced_stage", "foreign_mount"])
def test_changed_stage_is_never_published(stage, monkeypatch, change):
    args, temporary, final = stage
    if change == "different_bytes":
        temporary.write_bytes(b"z" * len(PAYLOAD))
    elif change == "replaced_stage":
        temporary.rename(temporary.with_suffix(".old"))
        temporary.write_bytes(PAYLOAD)
    else:
        monkeypatch.setattr(
            commit.linux_mount, "_fd_mount_id",
            lambda fd: 43 if fd == args["staged_fd"] else 42,
        )
    result = commit.publish_usb_staged_file(**args)
    assert result.status == "blocked"
    assert result.reason == "usb_publish_stage_unverified"
    assert not final.exists()


@pytest.mark.parametrize("answer", [False, None, 1])
def test_missing_current_pending_record_stops_before_publication(stage, answer):
    args, temporary, final = stage
    result = commit.publish_usb_staged_file(**(args | {"pending_is_current": lambda: answer}))
    assert result.reason == "usb_publish_pending_not_current"
    assert temporary.exists()
    assert not final.exists()


def test_directory_fsync_failure_keeps_final_bytes_for_recovery(stage, monkeypatch):
    args, temporary, final = stage
    fsync = os.fsync

    def sync(fd):
        if fd == args["directory_fd"]:
            raise OSError(errno.EIO, "synthetic volume unplugged")
        fsync(fd)

    monkeypatch.setattr(commit.os, "fsync", sync)
    result = commit.publish_usb_staged_file(**args)
    assert result == commit.UsbFilePublication(
        "recovery_required", "usb_publish_io_failed", errno.EIO,
    )
    assert not temporary.exists()
    assert final.read_bytes() == PAYLOAD


def test_revocation_after_rename_requires_recovery_without_unlink(stage):
    args, temporary, final = stage
    result = commit.publish_usb_staged_file(
        **(args | {"pending_is_current": temporary.exists}),
    )
    assert result == commit.UsbFilePublication("recovery_required", "usb_publish_postcheck_failed")
    assert final.read_bytes() == PAYLOAD


def test_no_fallback_when_native_noreplace_is_unsupported(stage, monkeypatch):
    args, temporary, final = stage

    def unsupported(*_):
        raise OSError(errno.EOPNOTSUPP, "synthetic unsupported filesystem")

    monkeypatch.setattr(commit, "_rename_exclusive", unsupported)
    result = commit.publish_usb_staged_file(**args)
    assert result == commit.UsbFilePublication(
        "blocked", "usb_publish_unsupported", errno.EOPNOTSUPP,
    )
    assert temporary.read_bytes() == PAYLOAD
    assert not final.exists()


def test_final_replacement_with_identical_bytes_does_not_publish_a_receipt(stage):
    args, temporary, final = stage

    def current():
        if final.exists():
            final.rename(final.with_suffix(".relocated"))
            final.write_bytes(PAYLOAD)
        return True

    result = commit.publish_usb_staged_file(**(args | {"pending_is_current": current}))
    assert result.status == "recovery_required"
    assert final.read_bytes() == PAYLOAD
    assert final.with_suffix(".relocated").read_bytes() == PAYLOAD


def test_error_reported_after_native_rename_is_uncertain_and_never_rolled_back(stage, monkeypatch):
    args, temporary, final = stage
    native = commit._rename_exclusive

    def ambiguous(*rename_args):
        native(*rename_args)
        raise OSError(errno.EIO, "synthetic error after namespace change")

    monkeypatch.setattr(commit, "_rename_exclusive", ambiguous)
    result = commit.publish_usb_staged_file(**args)
    assert result == commit.UsbFilePublication(
        "recovery_required", "usb_publish_io_failed", errno.EIO,
    )
    assert not temporary.exists()
    assert final.read_bytes() == PAYLOAD


def test_failed_pending_read_after_rename_reports_recovery_without_private_error(stage):
    args, temporary, final = stage

    class PendingUnavailable(Exception):
        pass

    def current():
        if final.exists():
            raise PendingUnavailable("receipt backend unavailable")
        return True

    result = commit.publish_usb_staged_file(**(args | {"pending_is_current": current}))
    assert result == commit.UsbFilePublication(
        "recovery_required", "usb_publish_revalidation_failed",
    )
    assert final.read_bytes() == PAYLOAD
