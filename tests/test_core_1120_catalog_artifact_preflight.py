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
