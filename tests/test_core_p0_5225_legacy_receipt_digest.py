"""#5225: legacy receipt reuse is bound to the bytes it actually attested."""
import hashlib
import os
from types import SimpleNamespace as NS

import pytest

from tests.test_core_1120_loan_artifact_state import _row, _inspect


@pytest.mark.parametrize("change", ["none", "same_size", "replacement", "alias",
                                      "parent_alias", "malformed_digest", "legacy"])
def test_saved_digest_is_not_a_filename_hint(tmp_path, change):
    folder = tmp_path / "library"
    folder.mkdir()
    path = folder / "book.epub"
    path.write_bytes(b"original")
    row = _row(path)
    if change != "legacy":
        row["artifact_id"] = "sha256:" + hashlib.sha256(b"original").hexdigest()
    if change == "same_size":
        old = path.stat()
        path.write_bytes(b"modified")
        os.utime(path, ns=(old.st_atime_ns, old.st_mtime_ns))
    elif change == "replacement":
        path.rename(folder / "prior.epub")
        path.write_bytes(b"modified")
    elif change == "alias":
        path.rename(folder / "prior.epub")
        path.symlink_to(folder / "prior.epub")
    elif change == "parent_alias":
        folder.rename(tmp_path / "external")
        folder.symlink_to(tmp_path / "external", target_is_directory=True)
    elif change == "malformed_digest":
        row["artifact_id"] = "sha256:not-a-digest"
    calls = []

    def inspect(*args):
        calls.append(args)
        return NS(valid=True, protected=False)

    state = _inspect([row], inspector=inspect)
    expected = change in ("none", "legacy")
    assert state.complete_local is expected
    assert bool(calls) is expected
    assert state.row is row
    assert path.exists()


@pytest.mark.parametrize("with_digest", [True, False])
def test_bytes_mutated_during_inspection_cannot_suppress_get(tmp_path, with_digest):
    path = tmp_path / "book.epub"
    path.write_bytes(b"original")
    before = path.stat()
    row = _row(path)
    if with_digest:
        row["artifact_id"] = "sha256:" + hashlib.sha256(b"original").hexdigest()

    def inspect(*args):
        path.write_bytes(b"modified")
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        return NS(valid=True, protected=False)

    state = _inspect([row], inspector=inspect)
    assert not state.complete_local and state.inspection is None
    assert state.row is row and path.read_bytes() == b"modified"
