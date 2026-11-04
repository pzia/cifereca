"""P1 #2854: USB target physical preflight stays read-only/non-authorizing."""
from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from cifereca.core import device_usb_target_preflight as mod
from cifereca.core.device_usb_mount import LinuxUsbMount


SHA = sha256(b"synthetic").hexdigest()
ARTIFACT = "sha256:" + SHA
TARGET = "Books/Cifereca/" + SHA + ".epub"


def case(tmp_path, monkeypatch):
    mount = tmp_path / "device"
    target_dir = mount / "Books" / "Cifereca"
    target_dir.mkdir(parents=True)
    stat = mount.stat()
    binding = {
        "device_id": "alice-reader", "identity_id": "alice",
        "enabled": True, "target_directory": "Books/Cifereca",
        "metadata": {"usb_fingerprint": "uuid-alice-0001"},
    }
    artifact = {
        "artifact_id": ARTIFACT, "sha256": SHA,
        "size": 1024, "media_format": "epub", "kind": "file",
    }
    intent = {
        "device_id": "alice-reader", "artifact_id": ARTIFACT,
        "desired_state": "keep", "target_relative_path": TARGET,
        "metadata": {"intent_identity_id": "alice"},
    }
    proof = LinuxUsbMount(
        identity_id="alice", device_id="alice-reader",
        usb_fingerprint="uuid-alice-0001", mountpoint=mount,
        filesystem="ext4", major=os.major(stat.st_dev),
        minor=os.minor(stat.st_dev), checked_at=1800000000,
    )
    calls = []
    def observe(**kwargs):
        calls.append(kwargs)
        return proof
    monkeypatch.setattr(mod, "observe_linux_usb_mount", observe)
    payload = dict(
        identity_id="alice", binding=binding, mountpoint=mount,
        artifact=artifact, intention=intent,
    )
    return payload, target_dir, proof, calls


def test_attested_mount_checks_space_and_exact_missing_target_without_creating(
    tmp_path, monkeypatch,
):
    args, directory, proof, calls = case(tmp_path, monkeypatch)
    before = list(directory.iterdir())
    result = mod.preflight_usb_target(**args)
    assert result.status == "review"
    assert result.reason == "target_absent_space_observed"
    assert type(result.available_bytes) is int
    assert result.available_bytes >= args["artifact"]["size"]
    assert result.executable is False
    assert list(directory.iterdir()) == before
    assert len(calls) == 2
    assert all(c["binding"]["metadata"]["usb_fingerprint"] ==
               proof.usb_fingerprint for c in calls)


@pytest.mark.parametrize("field,value,reason", [
    ("identity_id", "bob", "target_scope_unverified"),
    ("binding", {"identity_id": "bob"}, "target_scope_unverified"),
    ("artifact", {"size": 0}, "target_artifact_unverified"),
    ("intention", {"desired_state": "remove"}, "target_intention_unverified"),
])
def test_forged_identity_artifact_or_intention_refused(
    tmp_path, monkeypatch, field, value, reason,
):
    args, directory, proof, calls = case(tmp_path, monkeypatch)
    if field in ("binding", "artifact", "intention"):
        args[field] = {**args[field], **value}
    else:
        args[field] = value
    result = mod.preflight_usb_target(**args)
    assert result.status == "blocked"
    assert result.reason == reason
    assert result.executable is False
    assert not calls


@pytest.mark.parametrize("bad", [
    {"target_relative_path": "Books/Cifereca/foreign.epub"},
    {"target_relative_path": "Books/Cifereca/" + SHA + ".kepub.epub"},
    {"metadata": {"intent_identity_id": "bob"}},
    {"device_id": "other-reader"},
])
def test_cross_device_wrong_variant_and_foreign_intentions_refused(
    tmp_path, monkeypatch, bad,
):
    args, directory, proof, calls = case(tmp_path, monkeypatch)
    args["intention"] = {**args["intention"], **bad}
    result = mod.preflight_usb_target(**args)
    assert result.reason == "target_intention_unverified"
    assert not result.executable
    assert not calls


def test_existing_exact_or_casefold_collision_prevents_overwrite(
    tmp_path, monkeypatch,
):
    args, directory, proof, calls = case(tmp_path, monkeypatch)
    file = directory / (SHA.upper() + ".EPUB")
    file.write_bytes(b"foreign")
    result = mod.preflight_usb_target(**args)
    assert result.reason == "target_occupied"
    assert file.read_bytes() == b"foreign"
    assert result.executable is False


def test_symlinked_managed_directory_cannot_pass_no_follow(
    tmp_path, monkeypatch,
):
    args, directory, proof, calls = case(tmp_path, monkeypatch)
    moved = tmp_path / "hidden-dir"
    directory.rename(moved)
    directory.symlink_to(moved, target_is_directory=True)
    result = mod.preflight_usb_target(**args)
    assert result.status == "blocked"
    assert result.reason == "target_io_unverified"
    assert not result.executable
    assert list(moved.iterdir()) == []


def test_wrong_uuid_mount_or_volume_swap_refused(tmp_path, monkeypatch):
    args, directory, proof, calls = case(tmp_path, monkeypatch)
    monkeypatch.setattr(mod, "observe_linux_usb_mount", lambda **_: replace(
        proof, usb_fingerprint="uuid-bob-0001",
    ))
    assert mod.preflight_usb_target(**args).reason == "target_mount_unverified"
    count = 0
    def replaced(**kwargs):
        nonlocal count
        count += 1
        return proof if count == 1 else replace(proof, minor=proof.minor + 1)
    monkeypatch.setattr(mod, "observe_linux_usb_mount", replaced)
    result = mod.preflight_usb_target(**args)
    assert result.reason == "target_mount_changed"
    assert result.executable is False
    assert count == 2


@pytest.mark.parametrize("flags,blocks,reason", [
    (0, 0, "target_space_insufficient"),
    (getattr(os, "ST_RDONLY", 1), 1000000000, "target_volume_read_only"),
])
def test_low_capacity_and_read_only_filesystem_refuse_even_if_empty(
    tmp_path, monkeypatch, flags, blocks, reason,
):
    args, directory, proof, calls = case(tmp_path, monkeypatch)
    monkeypatch.setattr(
        mod.os, "fstatvfs",
        lambda fd: SimpleNamespace(
            f_flag=flags, f_bavail=blocks, f_frsize=4096,
        ),
    )
    result = mod.preflight_usb_target(**args)
    assert result.status == "blocked"
    assert result.reason == reason
    assert result.executable is False


def test_bounded_directory_enumeration_does_not_claim_absence(
    tmp_path, monkeypatch,
):
    args, directory, proof, calls = case(tmp_path, monkeypatch)
    monkeypatch.setattr(mod, "_MAX_ENTRIES", 2)
    for name in ("first", "second", "third"):
        (directory / name).write_text("x", encoding="ascii")
    result = mod.preflight_usb_target(**args)
    assert result.reason == "target_directory_unbounded"
    assert result.executable is False


def test_no_mount_evidence_never_touches_usb_path(tmp_path, monkeypatch):
    args, directory, proof, calls = case(tmp_path, monkeypatch)
    monkeypatch.setattr(mod, "observe_linux_usb_mount", lambda **_: None)
    monkeypatch.setattr(
        mod.os, "open",
        lambda *_a, **_kw: pytest.fail("unexpected open without mount proof"),
    )
    result = mod.preflight_usb_target(**args)
    assert result.reason == "target_mount_unverified"
    assert result.executable is False
