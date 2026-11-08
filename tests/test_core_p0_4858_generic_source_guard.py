"""P0 #4858: typed-source fence must be provider-neutral and read-only."""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from types import SimpleNamespace

from cifereca.core.source_reference_guard import has_typed_source


def test_canonical_source_fence_is_provider_scoped_and_read_only():
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE source_references "
               "(provider_id TEXT, credential_id TEXT, edition_id TEXT)")
    db.executemany(
        "INSERT INTO source_references VALUES (?,?,?)",
        [("kobo", "credential", "edition"),
         ("numilog", "other", "edition")],
    )
    storage = SimpleNamespace(_lock=threading.RLock(), _connect=lambda: db)
    try:
        before = db.total_changes
        assert has_typed_source(storage, "kobo", "credential", "edition")
        assert has_typed_source(storage, "numilog", "other", "edition")
        assert not has_typed_source(storage, "numilog", "credential", "edition")
        assert not has_typed_source(storage, "kobo", "other", "edition")
        assert not has_typed_source(storage, "kobo", "credential", "other")
        for invalid in (None, "", [], 0):
            assert not has_typed_source(storage, invalid, "credential", "edition")
            assert not has_typed_source(storage, "kobo", invalid, "edition")
            assert not has_typed_source(storage, "kobo", "credential", invalid)
        assert db.total_changes == before
    finally:
        db.close()


def test_legacy_provider_named_source_guard_is_not_shipped():
    root = Path(__file__).resolve().parents[1]
    assert not (root / "src/cifereca/core/kobo_legacy_source_guard.py").exists()
