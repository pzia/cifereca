"""P0 #2847: the read-only exact-Placement SHA audit never follows a parent.

All files are synthetic. No provider I/O, SQLite write or file cleanup.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from cifereca.core.acquisition_audit import _file_proof


def _digest(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


def test_nofollow_fd_walk_accepts_exact_nested_regular_file(tmp_path):
    root = tmp_path / "Library"
    path = root / "A" / "Books" / "book.epub"
    path.parent.mkdir(parents=True)
    payload = b"synthetic EPUB bytes with exact hash"
    path.write_bytes(payload)
    assert _file_proof(
        root=root, directory="A", path=str(path),
        artifact_id=_digest(payload),
    ) == "verified_sha256"
    assert path.read_bytes() == payload


@pytest.mark.parametrize("link_kind", ["root", "parent", "leaf"])
def test_nofollow_fd_walk_rejects_symlinks_even_for_identical_bytes(
    tmp_path, link_kind,
):
    root = tmp_path / "Library"
    owned = root / "A"
    foreign = tmp_path / "Foreign"
    foreign.mkdir()
    payload = b"synthetic foreign profile bytes"
    source = foreign / "book.epub"
    source.write_bytes(payload)
    if link_kind == "root":
        root.symlink_to(foreign, target_is_directory=True)
        # The foreign tree is deliberately shaped like a valid local one.
        (foreign / "A").mkdir()
        (foreign / "A" / "book.epub").write_bytes(payload)
        candidate = root / "A" / "book.epub"
    elif link_kind == "parent":
        owned.mkdir(parents=True)
        (owned / "linked").symlink_to(foreign, target_is_directory=True)
        candidate = owned / "linked" / "book.epub"
    else:
        owned.mkdir(parents=True)
        candidate = owned / "book.epub"
        candidate.symlink_to(source)
    assert candidate.read_bytes() == payload
    assert _file_proof(
        root=root, directory="A", path=str(candidate),
        artifact_id=_digest(payload),
    ) == "invalid_placement"
    assert source.read_bytes() == payload


def test_nofollow_fd_walk_cannot_attest_a_swapped_parent_during_final_open(
    tmp_path, monkeypatch,
):
    root = tmp_path / "Library"
    owned = root / "A" / "Books"
    foreign = tmp_path / "Foreign"
    owned.mkdir(parents=True)
    foreign.mkdir()
    payload = b"synthetic A original"
    foreign_payload = b"synthetic B foreign"
    local = owned / "book.pdf"
    local.write_bytes(payload)
    (foreign / "book.pdf").write_bytes(foreign_payload)
    saved_parent = root / "A" / "Detached"
    original_open = os.open
    swapped = []

    def swap_before_final_open(path, flags, *args, **kwargs):
        if path == "book.pdf" and kwargs.get("dir_fd") is not None and not swapped:
            owned.rename(saved_parent)
            owned.symlink_to(foreign, target_is_directory=True)
            swapped.append(True)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", swap_before_final_open)
    verdict = _file_proof(
        root=root, directory="A", path=str(local),
        artifact_id=_digest(payload),
    )
    assert swapped == [True]
    assert verdict == "file_changed"
    assert (saved_parent / "book.pdf").read_bytes() == payload
    assert (foreign / "book.pdf").read_bytes() == foreign_payload


def test_nofollow_fd_walk_missing_is_not_a_positive_proof(tmp_path):
    root = tmp_path / "Library"
    (root / "A").mkdir(parents=True)
    missing = root / "A" / "gone.epub"
    assert _file_proof(
        root=root, directory="A", path=str(missing),
        artifact_id=_digest(b"any"),
    ) == "missing_file"
