"""P1 #2854: bounded USB managed-subtree snapshot with synthetic mount facts.

No device or privileged operation is executed. Fake mount evidence is used
only inside tests; production has no caller-supplied 'mounted' override.
"""
from __future__ import annotations

from hashlib import sha256
import os
from pathlib import Path
import stat

import pytest

import cifereca.core.device_usb_managed_scan as scanner
from cifereca.core.device_usb_mount import LinuxUsbMount
from cifereca.core.device_usb_preview import (
    UsbLocalArtifact, preview_usb_delta,
)


BOOK = b"synthetic USB EPUB file bytes"
DIGEST = sha256(BOOK).hexdigest()
DEST = "Books/Cifereca/first.epub"


def binding(*, identity="alice", device="reader-a",
            fingerprint="uuid-0001", enabled=True):
    return {
        "identity_id": identity, "device_id": device,
        "enabled": enabled, "target_directory": "Books/Cifereca",
        "metadata": {"usb_fingerprint": fingerprint},
    }


def intent(*, identity="alice", device="reader-a",
           artifact="artifact-a", path=DEST, desired="keep"):
    return {
        "device_id": device, "artifact_id": artifact,
        "target_relative_path": path, "desired_state": desired,
        "observed_state": "unknown",
        "metadata": {"intent_identity_id": identity},
    }


def fake_usb_attestation(monkeypatch, *, after_scan=lambda _mount: None):
    calls = []

    def observe(*, identity_id, binding, mountpoint):
        calls.append((identity_id, binding["device_id"], mountpoint))
        mount_id = 189
        if len(calls) > 1:
            mount_id += after_scan(mountpoint) or 0
        st = mountpoint.stat()
        return LinuxUsbMount(
            identity_id=identity_id, device_id=binding["device_id"],
            usb_fingerprint=binding["metadata"]["usb_fingerprint"],
            mountpoint=mountpoint, filesystem="vfat",
            major=os.major(st.st_dev), minor=os.minor(st.st_dev),
            checked_at=1770000000, mount_id=mount_id,
        )

    monkeypatch.setattr(scanner, "observe_linux_usb_mount", observe)
    monkeypatch.setattr(scanner.linux_mount, "_fd_mount_id", lambda fd: 189)
    return calls


def roots(tmp_path):
    volume = tmp_path / "mounted-synthetic-usb"
    managed = volume / "Books" / "Cifereca"
    managed.mkdir(parents=True)
    return volume, managed


def test_readonly_inventory_exact_files_and_foreign_files(tmp_path, monkeypatch):
    volume, managed = roots(tmp_path)
    (managed / "first.epub").write_bytes(BOOK)
    (managed / "untracked.epub").write_bytes(b"external reader content")
    calls = fake_usb_attestation(monkeypatch)
    before = {
        entry.name: entry.read_bytes()
        for entry in managed.iterdir()
    }
    observed = scanner.scan_managed_usb(
        identity_id="alice", binding=binding(),
        mountpoint=volume, placements=[intent()],
    )
    assert observed is not None and observed.complete is True
    assert observed.identity_id == "alice"
    assert observed.device_id == "reader-a"
    assert observed.fingerprint == "uuid-0001"
    assert len(calls) == 2
    assert observed.files["artifact-a"].relative_path == DEST
    assert observed.files["artifact-a"].sha256 == DIGEST
    assert observed.files["artifact-a"].regular_file is True
    untracked = {key: row for key, row in observed.files.items()
                 if key.startswith("untracked:")}
    assert len(untracked) == 1
    assert next(iter(untracked.values())).relative_path == (
        "Books/Cifereca/untracked.epub"
    )
    assert {entry.name: entry.read_bytes()
            for entry in managed.iterdir()} == before

    # No source or copied right is inferred from an on-device observation.
    row, = preview_usb_delta(
        identity_id="alice", binding=binding(),
        placements=[intent()],
        local={"artifact-a": UsbLocalArtifact("alice", DIGEST, True)},
        scan=observed, now=observed.observed_at,
    )
    assert (row.action, row.observed) == ("skip", "present")
    assert row.executable is False


def test_empty_managed_directory_is_genuinely_complete(tmp_path, monkeypatch):
    volume, _ = roots(tmp_path)
    fake_usb_attestation(monkeypatch)
    observed = scanner.scan_managed_usb(
        identity_id="alice", binding=binding(),
        mountpoint=volume, placements=[intent()],
    )
    assert observed is not None
    assert observed.complete is True
    assert observed.files == {}


def test_external_file_at_desired_path_never_disappears(tmp_path, monkeypatch):
    volume, managed = roots(tmp_path)
    (managed / "first.epub").write_bytes(b"foreign book on reader")
    fake_usb_attestation(monkeypatch)
    observed = scanner.scan_managed_usb(
        identity_id="alice", binding=binding(),
        mountpoint=volume, placements=[],
    )
    assert observed is not None
    assert len(observed.files) == 1
    assert next(iter(observed.files)).startswith("untracked:")
    assert next(iter(observed.files.values())).relative_path == DEST


def test_nested_directories_and_casefold_distinct_book_names(tmp_path, monkeypatch):
    volume, managed = roots(tmp_path)
    nested = managed / "shelf"
    nested.mkdir()
    (nested / "One.epub").write_bytes(BOOK)
    (nested / "Two.epub").write_bytes(BOOK + b"2")
    fake_usb_attestation(monkeypatch)
    observed = scanner.scan_managed_usb(
        identity_id="alice", binding=binding(),
        mountpoint=volume, placements=[
            intent(artifact="book-1", path="Books/Cifereca/shelf/One.epub"),
            intent(artifact="book-2", path="Books/Cifereca/shelf/Two.epub"),
        ],
    )
    assert observed is not None and observed.complete
    assert set(observed.files) == {"book-1", "book-2"}
    assert observed.files["book-1"].sha256 == DIGEST


@pytest.mark.parametrize("invalid", [
    {"identity_id": "bob"},
    {"binding": binding(identity="bob")},
    {"binding": binding(enabled=False)},
    {"binding": binding(enabled="true")},
    {"binding": binding(device="")},
    {"binding": {**binding(), "target_directory": "../escape"}},
    {"binding": {**binding(), "target_directory": "/home/private"}},
])
def test_binding_and_managed_scope_must_be_exact(tmp_path, monkeypatch, invalid):
    volume, _ = roots(tmp_path)
    fake_usb_attestation(monkeypatch)
    args = {"identity_id": "alice", "binding": binding(),
            "mountpoint": volume, "placements": [intent()]}
    args.update(invalid)
    assert scanner.scan_managed_usb(**args) is None


def test_mount_swap_during_walk_never_claims_complete(tmp_path, monkeypatch):
    volume, managed = roots(tmp_path)
    (managed / "first.epub").write_bytes(BOOK)
    calls = fake_usb_attestation(monkeypatch, after_scan=lambda _mount: 1)
    assert scanner.scan_managed_usb(
        identity_id="alice", binding=binding(),
        mountpoint=volume, placements=[intent()],
    ) is None
    assert len(calls) == 2


def test_real_unattested_directory_is_never_accepted_as_usb(tmp_path):
    volume, managed = roots(tmp_path)
    (managed / "first.epub").write_bytes(BOOK)
    # No monkeypatch: an ordinary directory is not a block USB mount.
    assert scanner.scan_managed_usb(
        identity_id="alice", binding=binding(),
        mountpoint=volume, placements=[intent()],
    ) is None


@pytest.mark.parametrize("hostile", [
    {"target_relative_path": "../secret.epub"},
    {"target_relative_path": "Books/Cifereca/../secret.epub"},
    {"target_relative_path": "Books/Cifereca/"},
    {"target_relative_path": "Other/a.epub"},
    {"metadata": {"intent_identity_id": "bob"}},
    {"device_id": "reader-bob"},
])
def test_foreign_or_unsafe_intentions_never_allow_a_full_scan(
    tmp_path, monkeypatch, hostile,
):
    volume, _ = roots(tmp_path)
    fake_usb_attestation(monkeypatch)
    row = intent()
    row.update(hostile)
    assert scanner.scan_managed_usb(
        identity_id="alice", binding=binding(),
        mountpoint=volume, placements=[row],
    ) is None


def test_legacy_intent_without_owner_cannot_be_relabelled(tmp_path, monkeypatch):
    volume, _ = roots(tmp_path)
    fake_usb_attestation(monkeypatch)
    row = intent()
    row.pop("metadata")
    assert scanner.scan_managed_usb(
        identity_id="alice", binding=binding(),
        mountpoint=volume, placements=[row],
    ) is None


def test_duplicate_targets_ambiguous_even_if_no_physical_file(
    tmp_path, monkeypatch,
):
    volume, _ = roots(tmp_path)
    fake_usb_attestation(monkeypatch)
    rows = [intent(artifact="a", path=DEST),
            intent(artifact="b", path="Books/Cifereca/FIRST.epub")]
    assert scanner.scan_managed_usb(
        identity_id="alice", binding=binding(),
        mountpoint=volume, placements=rows,
    ) is None


def test_symlinked_book_is_never_hashed_as_usb_content(tmp_path, monkeypatch):
    volume, managed = roots(tmp_path)
    outside = tmp_path / "private.txt"
    outside.write_text("must not be read")
    (managed / "first.epub").symlink_to(outside)
    fake_usb_attestation(monkeypatch)
    assert scanner.scan_managed_usb(
        identity_id="alice", binding=binding(),
        mountpoint=volume, placements=[intent()],
    ) is None


def test_symlinked_managed_subdirectory_does_not_escape_mount(tmp_path, monkeypatch):
    volume, managed = roots(tmp_path)
    outside = tmp_path / "external"
    outside.mkdir()
    (outside / "private.epub").write_bytes(BOOK)
    (managed / "out").symlink_to(outside, target_is_directory=True)
    fake_usb_attestation(monkeypatch)
    assert scanner.scan_managed_usb(
        identity_id="alice", binding=binding(),
        mountpoint=volume, placements=[intent()],
    ) is None


def test_symlinked_managed_root_does_not_claim_scan(tmp_path, monkeypatch):
    volume, managed = roots(tmp_path)
    managed.rmdir()
    outside = tmp_path / "external"
    outside.mkdir()
    managed.symlink_to(outside, target_is_directory=True)
    fake_usb_attestation(monkeypatch)
    assert scanner.scan_managed_usb(
        identity_id="alice", binding=binding(),
        mountpoint=volume, placements=[intent()],
    ) is None


def test_no_access_to_missing_managed_area_means_unknown(tmp_path, monkeypatch):
    volume, managed = roots(tmp_path)
    managed.rmdir()
    fake_usb_attestation(monkeypatch)
    assert scanner.scan_managed_usb(
        identity_id="alice", binding=binding(),
        mountpoint=volume, placements=[intent()],
    ) is None


def test_capped_file_count_aborts_instead_of_returning_partial(tmp_path, monkeypatch):
    volume, managed = roots(tmp_path)
    (managed / "first.epub").write_bytes(BOOK)
    fake_usb_attestation(monkeypatch)
    monkeypatch.setattr(scanner, "_MAX_FILES", 0)
    assert scanner.scan_managed_usb(
        identity_id="alice", binding=binding(),
        mountpoint=volume, placements=[],
    ) is None


def test_non_regular_special_file_aborts_scan(tmp_path, monkeypatch):
    if not hasattr(os, "mkfifo"):
        pytest.skip("FIFO not supported on this host")
    volume, managed = roots(tmp_path)
    fifo = managed / "special.fifo"
    os.mkfifo(fifo)
    fake_usb_attestation(monkeypatch)
    assert scanner.scan_managed_usb(
        identity_id="alice", binding=binding(),
        mountpoint=volume, placements=[],
    ) is None


def test_same_path_can_belong_to_one_intent_only(tmp_path, monkeypatch):
    volume, managed = roots(tmp_path)
    (managed / "first.epub").write_bytes(BOOK)
    fake_usb_attestation(monkeypatch)
    rows = [intent(), intent(artifact="different")]
    assert scanner.scan_managed_usb(
        identity_id="alice", binding=binding(),
        mountpoint=volume, placements=rows,
    ) is None


def test_new_unsited_intent_does_not_invent_a_device_path(tmp_path, monkeypatch):
    volume, managed = roots(tmp_path)
    (managed / "untracked.epub").write_bytes(BOOK)
    fake_usb_attestation(monkeypatch)
    row = intent()
    row["target_relative_path"] = None
    seen = scanner.scan_managed_usb(
        identity_id="alice", binding=binding(),
        mountpoint=volume, placements=[row],
    )
    assert seen is not None and seen.complete
    assert len(seen.files) == 1
    assert next(iter(seen.files)).startswith("untracked:")
    # The companion preview correction #4399 prohibits missing/unplanned
    # target from being treated as an upload candidate.


def test_two_physical_files_with_casefold_colliding_paths_invalidate_scan(
    tmp_path, monkeypatch,
):
    volume, managed = roots(tmp_path)
    (managed / "First.epub").write_bytes(BOOK)
    (managed / "first.epub").write_bytes(BOOK + b"2")
    fake_usb_attestation(monkeypatch)
    assert scanner.scan_managed_usb(
        identity_id="alice", binding=binding(),
        mountpoint=volume, placements=[],
    ) is None


def test_large_usb_directory_is_bounded_before_entries_are_materialized(
    tmp_path, monkeypatch,
):
    volume, _managed = roots(tmp_path)
    fake_usb_attestation(monkeypatch)
    requested = {"count": 0}

    class GuardedDirectoryListing:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def __iter__(self):
            return self

        def __next__(self):
            requested["count"] += 1
            if requested["count"] > 3:
                raise AssertionError("unbounded scandir materialization")
            # Entry properties must never be accessed because exceeding the
            # enumeration cap aborts *before* processing any observed file.
            return object()

    with monkeypatch.context() as scoped:
        scoped.setattr(scanner, "_MAX_FILES", 1)
        scoped.setattr(scanner, "_MAX_DIRS", 1)
        scoped.setattr(scanner.os, "scandir", lambda fd: GuardedDirectoryListing())
        observed = scanner.scan_managed_usb(
            identity_id="alice", binding=binding(),
            mountpoint=volume, placements=[],
        )
    assert observed is None
    assert requested["count"] == 3


def test_same_device_bind_mounted_file_is_not_read(tmp_path, monkeypatch):
    volume, managed = roots(tmp_path)
    (managed / "first.epub").write_bytes(BOOK)
    fake_usb_attestation(monkeypatch)

    def mount_id(fd):
        return 190 if stat.S_ISREG(os.fstat(fd).st_mode) else 189

    monkeypatch.setattr(scanner.linux_mount, "_fd_mount_id", mount_id)
    monkeypatch.setattr(scanner.os, "read", lambda *_: pytest.fail("read foreign file"))
    assert scanner.scan_managed_usb(
        identity_id="alice", binding=binding(),
        mountpoint=volume, placements=[intent()],
    ) is None


def test_replaced_managed_path_invalidates_complete_inventory(tmp_path, monkeypatch):
    volume, managed = roots(tmp_path)
    (managed / "first.epub").write_bytes(BOOK)

    def replace_managed(_mount):
        managed.rename(managed.with_name("moved"))
        managed.mkdir()

    fake_usb_attestation(monkeypatch, after_scan=replace_managed)
    assert scanner.scan_managed_usb(
        identity_id="alice", binding=binding(),
        mountpoint=volume, placements=[intent()],
    ) is None
