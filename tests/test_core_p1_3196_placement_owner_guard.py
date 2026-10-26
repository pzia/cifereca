"""#3196: a canonical placement path is not authority to rebind Kobo owners.

Synthetic SQLite rows only; no file mutation, provider/network or DRM.
"""
from __future__ import annotations

import pytest

from cifereca.storage import Storage


def _book(*, identity="alice", credential="card-a", item="book-a",
          path=None, sha="a" * 64, status="pending"):
    return {
        "provider_id": "kobo",
        "credential_id": credential,
        "identity_id": identity,
        "account_id": "reader",
        "item_id": item,
        "entitlement_type": "owned",
        "title": item,
        "format": "epub",
        "sha256": sha,
        "size": 99,
        "local_path": str(path) if path is not None else None,
        "status": status,
    }


def _snapshot(storage):
    return {
        "legacy": storage.backup_list("kobo"),
        "rights": storage.entitlements(provider_id="kobo"),
        "acquisitions": storage.acquisitions(provider_id="kobo"),
        "artifacts": storage.artifacts(),
        "placements": storage.placements(),
        "catalogue": storage.catalog_entries(),
    }


@pytest.mark.parametrize("other", [
    {"identity": "bob", "credential": "card-b", "item": "book-b"},
    {"identity": "alice", "credential": "card-b", "item": "book-b"},
    {"identity": "alice", "credential": "card-a", "item": "book-b"},
])
def test_distinct_kobo_entitlement_cannot_take_over_same_path(tmp_path, other):
    storage = Storage(tmp_path / "state.sqlite3")
    same_path = tmp_path / "library" / "single-file.epub"
    try:
        storage.backup_put(_book(path=same_path))
        before = _snapshot(storage)
        storage.backup_put(_book(path=same_path, **other))
        after = _snapshot(storage)
        # Both provider declarations remain (diagnostics/migration parity).
        assert len(after["legacy"]) == 2
        assert len(after["rights"]) == 2
        assert len(after["acquisitions"]) == 2
        # No second LOCAL claim or takeover by the newly seen book.
        assert len(after["placements"]) == len(before["placements"]) == 1
        assert after["placements"] == before["placements"]
        assert after["catalogue"] == before["catalogue"]
        acquired = next(x for x in after["acquisitions"]
                        if x["item_id"] == other["item"])
        assert acquired["artifact_id"] is None
        assert storage.placements()[0]["identity_id"] == "alice"
    finally:
        storage.close()


def test_repeated_exact_owner_can_refresh_placement_idempotently(tmp_path):
    storage = Storage(tmp_path / "state.sqlite3")
    path = tmp_path / "library" / "same.epub"
    try:
        storage.backup_put(_book(path=path))
        storage.backup_put(_book(path=path, status="complete"))
        assert len(storage.backup_list("kobo")) == 1
        assert len(storage.entitlements(provider_id="kobo")) == 1
        assert len(storage.acquisitions(provider_id="kobo")) == 1
        assert len(storage.placements()) == 1
        assert storage.placements()[0]["source_item_id"] == "book-a"
        assert storage.acquisitions(provider_id="kobo")[0]["status"] == "complete"
    finally:
        storage.close()


def test_distinct_physical_paths_remain_independent_across_identities(tmp_path):
    storage = Storage(tmp_path / "state.sqlite3")
    try:
        storage.backup_put(_book(path=tmp_path / "alice.epub"))
        storage.backup_put(_book(identity="bob", credential="card-b", item="book-b",
                                 path=tmp_path / "bob.epub"))
        assert len(storage.placements()) == 2
        assert {r["identity_id"] for r in storage.placements()} == {"alice", "bob"}
    finally:
        storage.close()


def test_legacy_unscoped_placement_is_not_auto_attested_as_new_owner(tmp_path):
    storage = Storage(tmp_path / "state.sqlite3")
    path = tmp_path / "legacy.epub"
    try:
        storage.backup_put(_book(identity=None, path=path))
        storage.backup_put(_book(identity="alice", path=path))
        assert len(storage.placements()) == 1
        # A legacy NULL owner does not turn into an attested placement owner
        # merely because a later compatibility projection named the path.
        assert storage.placements()[0]["identity_id"] is None
    finally:
        storage.close()
