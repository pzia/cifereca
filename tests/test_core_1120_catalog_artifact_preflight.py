"""P0 #1120: catalog-local artifact preflight remains read-only and scoped."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from cifereca.core.loan_artifact_state import (
    inspect_catalog_local_file,
    local_catalog_candidates,
)


def _entry(path: Path | None, **overrides):
    entry = {
        "identity_id": "alice", "provider_id": "paris", "item_id": "book-a",
        "local_state": "local",
        "metadata": {"path": str(path)} if path is not None else {},
    }
    return {**entry, **overrides}


def test_catalog_candidates_have_exact_identity_provider_item_and_local_state(tmp_path):
    matching_a = _entry(tmp_path / "a.epub")
    matching_b = _entry(tmp_path / "b.pdf")
    rows = [
        _entry(None, identity_id="bob"),
        _entry(None, provider_id="kobo"),
        _entry(None, item_id="book-b"),
        _entry(None, local_state="invalid"),
        matching_a,
        matching_b,
    ]
    snapshot = deepcopy(rows)
    selected = local_catalog_candidates(
        rows, identity_id="alice", source_provider="paris", item_id="book-a",
    )
    assert selected == [matching_a, matching_b]
    assert selected[0] is matching_a and selected[1] is matching_b
    assert rows == snapshot


@pytest.mark.parametrize(
    ("valid", "protected", "usable"),
    [
        (True, False, True),
        (True, None, True),
        (True, True, False),
        (False, False, False),
        (False, True, False),
    ],
)
def test_catalog_file_inspector_accepts_only_valid_unprotected_artifact(
    tmp_path, valid, protected, usable,
):
    path = tmp_path / "stored.epub"
    path.write_bytes(b"local fixture only")
    entry = _entry(path)
    original = deepcopy(entry)
    calls = []

    def inspector(received):
        calls.append(received)
        return SimpleNamespace(valid=valid, protected=protected)

    result, returned_path = inspect_catalog_local_file(
        entry, artifact_inspector=inspector,
    )
    assert result is usable
    assert returned_path == path
    assert calls == [path]  # No inferred format argument for catalog rows.
    assert entry == original
    assert path.read_bytes() == b"local fixture only"


@pytest.mark.parametrize("path_present", [False, True])
def test_missing_catalog_file_never_triggers_inspector(tmp_path, path_present):
    missing = tmp_path / "missing.epub" if path_present else None
    entry = _entry(missing)

    def forbidden(_path):
        pytest.fail("No artifact inspection when the file is absent")

    before = deepcopy(entry)
    usable, path = inspect_catalog_local_file(entry, artifact_inspector=forbidden)
    assert usable is False
    assert path == missing
    assert entry == before
    assert not (tmp_path / "missing.epub").exists()


def test_inspector_failure_propagates_before_caller_invalidates_any_row(tmp_path):
    path = tmp_path / "loan.pdf"
    path.write_bytes(b"fixture")
    entry = _entry(path)

    def failure(_path):
        raise OSError("inspection failed")

    with pytest.raises(OSError, match="inspection failed"):
        inspect_catalog_local_file(entry, artifact_inspector=failure)
    assert entry["local_state"] == "local"
    assert path.read_bytes() == b"fixture"


def test_nonlocal_rows_are_not_accidentally_resurrected(tmp_path):
    entries = [_entry(tmp_path / "missing.epub", local_state="missing")]
    assert local_catalog_candidates(
        entries, identity_id="alice", source_provider="paris", item_id="book-a",
    ) == []


@pytest.mark.parametrize("kind", ["file_alias", "parent_alias", "fifo", "directory"])
def test_catalog_preflight_never_inspects_non_regular_or_aliased_path(tmp_path, kind):
    import os

    folder = tmp_path / "library"
    folder.mkdir()
    path = folder / "book.epub"
    if kind == "file_alias":
        target = tmp_path / "external.epub"
        target.write_bytes(b"outside")
        path.symlink_to(target)
    elif kind == "parent_alias":
        target = tmp_path / "outside"
        target.mkdir()
        (target / path.name).write_bytes(b"outside")
        folder.rmdir()
        folder.symlink_to(target, target_is_directory=True)
    elif kind == "fifo":
        os.mkfifo(path)
    else:
        path.mkdir()

    def forbidden(_path):
        pytest.fail("Invalid catalog path must not be inspected")

    assert inspect_catalog_local_file(_entry(path), artifact_inspector=forbidden) == (False, path)


@pytest.mark.parametrize("change", ["replace", "same_size", "parent_replace"])
def test_catalog_preflight_refuses_changed_physical_witness(tmp_path, change):
    import os

    folder = tmp_path / "library"
    folder.mkdir()
    path = folder / "book.epub"
    path.write_bytes(b"original")
    original = path.stat()

    def inspector(_path):
        if change == "replace":
            path.rename(folder / "previous.epub")
            path.write_bytes(b"original")
        elif change == "same_size":
            path.write_bytes(b"modified")
            os.utime(path, ns=(original.st_atime_ns, original.st_mtime_ns))
        else:
            folder.rename(tmp_path / "previous")
            folder.mkdir()
            path.write_bytes(b"original")
        return SimpleNamespace(valid=True, protected=False)

    assert inspect_catalog_local_file(_entry(path), artifact_inspector=inspector) == (False, path)
    assert path.exists()


def test_catalog_preflight_requires_boolean_validity(tmp_path):
    path = tmp_path / "book.epub"
    path.write_bytes(b"fixture")
    assert inspect_catalog_local_file(
        _entry(path),
        artifact_inspector=lambda _: SimpleNamespace(valid="yes", protected=False),
    ) == (False, path)


@pytest.mark.parametrize("address", ["exact", "different", "malformed", "nontext", "empty"])
def test_catalog_preflight_honors_explicit_content_address(tmp_path, address):
    import hashlib

    path = tmp_path / "loan.epub"
    path.write_bytes(b"actual book")
    digest = hashlib.sha256(b"actual book").hexdigest()
    artifact_id = {
        "exact": "sha256:" + digest,
        "different": "sha256:" + hashlib.sha256(b"other book").hexdigest(),
        "malformed": "sha256:xyz",
        "nontext": True,
        "empty": "",
    }[address]
    entry = _entry(path, artifact_id=artifact_id)
    before = deepcopy(entry)
    calls = []

    def inspect(received):
        calls.append(received)
        return SimpleNamespace(valid=True, protected=False)

    assert inspect_catalog_local_file(entry, artifact_inspector=inspect) == (address == "exact", path)
    assert calls == ([path] if address == "exact" else [])
    assert entry == before
    assert path.read_bytes() == b"actual book"


def test_catalog_content_address_rechecks_after_inspection(tmp_path):
    import hashlib

    path = tmp_path / "loan.epub"
    path.write_bytes(b"actual book")
    entry = _entry(path, artifact_id="sha256:" + hashlib.sha256(path.read_bytes()).hexdigest())

    def inspect(received):
        received.write_bytes(b"other book!")
        return SimpleNamespace(valid=True, protected=False)

    assert inspect_catalog_local_file(entry, artifact_inspector=inspect) == (False, path)
