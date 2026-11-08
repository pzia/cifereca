"""P1 #4983: bounded, read-only observation of caller-selected USB paths."""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cifereca_storage_usb import PLUGIN, observe_usb_files


def test_two_device_profiles_and_verified_bytes(tmp_path):
    service = PLUGIN.activate(None)
    a = tmp_path / "reader-a"
    b = tmp_path / "reader-b"
    for root in (a, b):
        (root / "cifereca").mkdir(parents=True)
    (a / "cifereca" / "book.epub").write_bytes(b"ebook-a")
    (b / "cifereca" / "book.epub").write_bytes(b"ebook-b")
    first = service.observe_usb_files(a, ("cifereca/book.epub", "cifereca/absent.epub"))
    second = service.observe_usb_files(b, ("cifereca/book.epub",))
    assert [(row.status, row.size) for row in first] == [("present", 7), ("missing", None)]
    assert first[0].sha256 == sha256(b"ebook-a").hexdigest()
    assert second[0].sha256 == sha256(b"ebook-b").hexdigest()
    assert first[0].sha256 != second[0].sha256


def test_symlinks_and_special_objects_never_attest_presence(tmp_path):
    (tmp_path / "managed").mkdir()
    (tmp_path / "outside.epub").write_bytes(b"outside")
    (tmp_path / "managed" / "link.epub").symlink_to(tmp_path / "outside.epub")
    (tmp_path / "alias").symlink_to(tmp_path / "managed", target_is_directory=True)
    rows = observe_usb_files(tmp_path, ("managed/link.epub", "alias/no.epub", "managed"))
    assert [row.status for row in rows] == ["unverified"] * 3
    assert all(row.sha256 is None for row in rows)


@pytest.mark.parametrize("name", (
    "../escape", "/absolute", "a//b", "./book", "a/./b", "a/../b",
    "x\\y", "C:drive", "bad\x00name",
))
def test_path_traversal_rejected_before_read(tmp_path, name):
    with pytest.raises(ValueError, match="usb_observation_path_invalid"):
        observe_usb_files(tmp_path, (name,))


def test_collisions_rejected_and_absent_root_is_not_missing(tmp_path):
    with pytest.raises(ValueError, match="usb_observation_paths_collide"):
        observe_usb_files(tmp_path, ("Books/a.epub", "books/A.EPUB"))
    assert observe_usb_files(tmp_path / "removed", ("book.epub",))[0].status == "unverified"


def test_observation_does_not_modify_files(tmp_path):
    root = tmp_path / "reader"
    root.mkdir()
    file = root / "book.pdf"
    file.write_bytes(b"%PDF-offline")
    before = file.stat()
    row = observe_usb_files(root, ("book.pdf",))[0]
    assert row.sha256 == sha256(b"%PDF-offline").hexdigest()
    assert row.status == "present"
    assert file.read_bytes() == b"%PDF-offline"
    assert file.stat().st_mtime_ns == before.st_mtime_ns


def test_large_file_and_batch_are_bounded(tmp_path):
    from cifereca_storage_usb import _MAX_TOTAL_BYTES
    oversized = tmp_path / "huge.epub"
    with oversized.open("wb") as stream:
        stream.truncate(_MAX_TOTAL_BYTES + 1)
    assert observe_usb_files(tmp_path, ("huge.epub",))[0].status == "unverified"
    with pytest.raises(ValueError, match="usb_observation_request_invalid"):
        observe_usb_files(tmp_path, ("unrelated",) * 257)


def test_rename_during_read_is_unverified(tmp_path, monkeypatch):
    import os
    import cifereca_storage_usb as usb
    root = tmp_path / "reader"
    root.mkdir()
    (root / "book.epub").write_bytes(b"original")
    real_read = os.read
    changed = False

    def swap(fd, size):
        nonlocal changed
        chunk = real_read(fd, size)
        if chunk and not changed:
            changed = True
            (root / "replacement.epub").write_bytes(b"replaced")
            (root / "replacement.epub").replace(root / "book.epub")
        return chunk

    monkeypatch.setattr(usb.os, "read", swap)
    result = usb.observe_usb_files(root, ("book.epub",))
    assert changed
    assert result[0].status == "unverified"
    assert result[0].sha256 is None



def test_growing_file_never_causes_unbounded_read(tmp_path, monkeypatch):
    import os
    import cifereca_storage_usb as usb

    book = tmp_path / "book.epub"
    book.write_bytes(b"a")
    original_read = os.read
    observed_limits = []

    def continuously_growing_read(fd, limit):
        observed_limits.append(limit)
        if len(observed_limits) > 3:
            raise AssertionError("observation consumed an unbounded growing file")
        with book.open("ab") as writer:
            writer.write(b"x" * (1024 * 1024))
        return original_read(fd, limit)

    monkeypatch.setattr(usb.os, "read", continuously_growing_read)
    row = usb.observe_usb_files(tmp_path, ("book.epub",))[0]
    assert row.status == "unverified"
    assert row.sha256 is None
    assert row.size is None
    assert observed_limits == [2]


def test_hardlinked_file_does_not_become_managed_receipt(tmp_path):
    """#4983: same bytes in another tree do not establish managed ownership."""
    import os

    managed = tmp_path / "managed"
    managed.mkdir()
    outside = tmp_path / "outside.epub"
    outside.write_bytes(b"same bytes, different owner")
    os.link(outside, managed / "book.epub")
    row = observe_usb_files(managed, ("book.epub",))[0]
    assert row.status == "unverified"
    assert row.sha256 is None and row.size is None
    assert outside.read_bytes() == b"same bytes, different owner"


def test_symlinked_selected_root_is_not_attested(tmp_path):
    """#4983: the caller's apparent USB root may not redirect observation."""
    real_root = tmp_path / "device"
    real_root.mkdir()
    (real_root / "book.epub").write_bytes(b"private device")
    link_root = tmp_path / "swapped-device"
    link_root.symlink_to(real_root, target_is_directory=True)
    row = observe_usb_files(link_root, ("book.epub",))[0]
    assert row.status == "unverified"
    assert row.sha256 is None
    assert (real_root / "book.epub").read_bytes() == b"private device"


def test_empty_regular_file_is_observation_only_not_transfer_receipt(tmp_path):
    """#4983: zero-byte files can be observed without granting ownership."""
    (tmp_path / "empty.epub").write_bytes(b"")
    row = observe_usb_files(tmp_path, ("empty.epub",))[0]
    assert row.status == "present"
    assert row.size == 0 and row.sha256 == sha256(b"").hexdigest()


def test_parent_directory_replaced_during_read_is_unverified(tmp_path, monkeypatch):
    import os
    import cifereca_storage_usb as usb

    managed = tmp_path / "managed"
    managed.mkdir()
    (managed / "book.epub").write_bytes(b"original")
    original_read = os.read
    replaced = False

    def replace_parent_after_first_read(fd, limit):
        nonlocal replaced
        chunk = original_read(fd, limit)
        if chunk and not replaced:
            replaced = True
            managed.rename(tmp_path / "detached")
            managed.mkdir()
            (managed / "book.epub").write_bytes(b"original")
        return chunk

    monkeypatch.setattr(usb.os, "read", replace_parent_after_first_read)
    row = usb.observe_usb_files(tmp_path, ("managed/book.epub",))[0]
    assert replaced
    assert row.status == "unverified"
    assert row.sha256 is None


def test_parent_directory_replaced_by_symlink_during_read_is_unverified(tmp_path, monkeypatch):
    import os
    import cifereca_storage_usb as usb

    managed = tmp_path / "managed"
    managed.mkdir()
    (managed / "book.epub").write_bytes(b"original")
    original_read = os.read

    def swap_directory_for_symlink(fd, limit):
        chunk = original_read(fd, limit)
        if chunk and not (tmp_path / "detached").exists():
            managed.rename(tmp_path / "detached")
            managed.symlink_to(tmp_path / "detached", target_is_directory=True)
        return chunk

    monkeypatch.setattr(usb.os, "read", swap_directory_for_symlink)
    row = usb.observe_usb_files(tmp_path, ("managed/book.epub",))[0]
    assert row.status == "unverified"
    assert row.sha256 is None


def test_selected_usb_root_replaced_during_read_is_unverified(tmp_path, monkeypatch):
    import os
    import cifereca_storage_usb as usb

    root = tmp_path / "device"
    root.mkdir()
    (root / "book.epub").write_bytes(b"original")
    original_read = os.read

    def swap_root(fd, limit):
        chunk = original_read(fd, limit)
        if chunk and not (tmp_path / "detached").exists():
            root.rename(tmp_path / "detached")
            root.mkdir()
            (root / "book.epub").write_bytes(b"original")
        return chunk

    monkeypatch.setattr(usb.os, "read", swap_root)
    row = usb.observe_usb_files(root, ("book.epub",))[0]
    assert row.status == "unverified"
    assert row.sha256 is None


def test_selected_usb_root_removed_during_read_is_unverified(tmp_path, monkeypatch):
    import os
    import cifereca_storage_usb as usb

    root = tmp_path / "device"
    root.mkdir()
    (root / "book.epub").write_bytes(b"original")
    original_read = os.read

    def detach_root(fd, limit):
        chunk = original_read(fd, limit)
        if chunk and not (tmp_path / "detached").exists():
            root.rename(tmp_path / "detached")
        return chunk

    monkeypatch.setattr(usb.os, "read", detach_root)
    row = usb.observe_usb_files(root, ("book.epub",))[0]
    assert row.status == "unverified"
    assert row.sha256 is None
