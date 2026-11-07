"""The storage.target hook receives only bytes and a bounded Core-owned sink."""
from contextlib import ExitStack
import errno
from hashlib import sha256
import os
from types import SimpleNamespace

import pytest

from cifereca.core import device_usb_file_staging as stage
from cifereca.core.device_usb_file_commit import publish_usb_staged_file
from cifereca.plugin_api import USB_STAGE_CAPABILITY, Cancelled


PAYLOAD = b"immutable synthetic USB payload"
DIGEST = sha256(PAYLOAD).hexdigest()
STAGED = ".cifereca-usb-" + "b" * 32 + ".part"
TARGET = DIGEST + ".epub"


@pytest.fixture
def volume(tmp_path, monkeypatch):
    def write_usb_stage(payload, append):
        append(payload[:8])
        append(payload[8:])

    service = SimpleNamespace(write_usb_stage=write_usb_stage)
    resolution = SimpleNamespace(
        plugin_id="test.storage.usb", registry_generation=12,
        capabilities=(USB_STAGE_CAPABILITY,), service=service,
    )
    rows = [resolution]

    def services(category):
        assert category == "storage.target"
        return tuple(rows)

    monkeypatch.setattr(stage.linux_mount, "_fd_mount_id", lambda _: 42)
    with ExitStack() as stack:
        directory_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
        stack.callback(os.close, directory_fd)
        yield dict(
            plugin_manager=SimpleNamespace(services=services),
            plugin_id=resolution.plugin_id, registry_generation=resolution.registry_generation,
            directory_fd=directory_fd, mount_id=42, staged_name=STAGED,
            payload=PAYLOAD, expected_sha256=DIGEST, pending_is_current=lambda: True,
        ), tmp_path, rows


def test_storage_target_stages_then_core_commits_without_overwrite(volume):
    args, root, _ = volume
    result = stage.stage_usb_file(**args)
    assert result.status == "staged"
    assert type(result.staged_fd) is int
    try:
        assert (root / STAGED).read_bytes() == PAYLOAD
        assert not (root / TARGET).exists()
        published = publish_usb_staged_file(
            directory_fd=args["directory_fd"], staged_fd=result.staged_fd,
            mount_id=args["mount_id"], staged_name=STAGED, target_name=TARGET,
            expected_sha256=DIGEST, size=len(PAYLOAD),
            pending_is_current=args["pending_is_current"],
        )
        assert published.status == "published"
        assert (root / TARGET).read_bytes() == PAYLOAD
        assert not (root / STAGED).exists()
    finally:
        os.close(result.staged_fd)


@pytest.mark.parametrize("condition", ["absent", "inactive_generation", "no_capability"])
def test_unavailable_storage_target_never_creates_stage(volume, condition):
    args, root, rows = volume
    if condition == "absent":
        rows.clear()
    elif condition == "inactive_generation":
        rows[0].registry_generation += 1
    else:
        rows[0].capabilities = ()
    result = stage.stage_usb_file(**args)
    assert result.reason == "usb_stage_transport_unavailable"
    assert list(root.iterdir()) == []


def test_existing_stage_is_never_adopted_or_replaced(volume):
    args, root, _ = volume
    (root / STAGED).write_bytes(b"previous attempt")
    result = stage.stage_usb_file(**args)
    assert result == stage.UsbFileStaging(
        "recovery_required", "usb_stage_already_exists", errno=errno.EEXIST,
    )
    assert (root / STAGED).read_bytes() == b"previous attempt"


def test_corrupt_plugin_bytes_never_reach_the_stage(volume):
    args, root, rows = volume
    rows[0].service.write_usb_stage = lambda payload, append: append(b"foreign")
    result = stage.stage_usb_file(**args)
    assert result.status == "recovery_required"
    assert result.staged_fd is None
    assert (root / STAGED).read_bytes() == b""
    assert not (root / TARGET).exists()


def test_replaced_registry_after_write_requires_recovery(volume):
    args, root, rows = volume

    def write(payload, append):
        append(payload)
        rows[0].registry_generation += 1

    rows[0].service.write_usb_stage = write
    result = stage.stage_usb_file(**args)
    assert result.reason == "usb_stage_scope_changed"
    assert result.staged_fd is None
    assert (root / STAGED).read_bytes() == PAYLOAD


def test_cancelled_after_first_chunk_leaves_only_recoverable_stage(volume):
    args, root, _ = volume
    checks = 0

    def cancel():
        nonlocal checks
        checks += 1
        if checks == 3:
            raise Cancelled("user cancelled")

    result = stage.stage_usb_file(**(args | {"check_cancelled": cancel}))
    assert result.status == "recovery_required"
    assert result.detail == "user cancelled"
    assert (root / STAGED).read_bytes() == PAYLOAD[:8]
    assert not (root / TARGET).exists()


def test_expired_sink_cannot_write_after_return(volume):
    args, root, rows = volume
    saved = []

    def write(payload, append):
        append(payload)
        saved.append(append)

    rows[0].service.write_usb_stage = write
    result = stage.stage_usb_file(**args)
    assert result.status == "staged"
    try:
        with pytest.raises(ValueError, match="outside its synchronous call"):
            saved[0](b"extra")
        assert (root / STAGED).read_bytes() == PAYLOAD
    finally:
        os.close(result.staged_fd)
