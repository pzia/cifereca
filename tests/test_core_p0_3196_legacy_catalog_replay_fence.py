"""#3196 — passive backup replay cannot take back a promoted catalogue row.

All tests are local SQLite fixtures. No provider, source rights, download,
runtime ADEPT, or production library is contacted.
"""
from __future__ import annotations

import json

import pytest

from cifereca.storage import Storage


def _legacy(path, *, sha="a" * 64, title="Old manifest", timestamp=100):
    return {
        "provider_id": "kobo", "credential_id": "credential-A",
        "account_id": "account-A", "identity_id": "A",
        "item_id": "edition-A", "entitlement_type": "owned",
        "title": title, "format": "epub", "status": "complete",
        "local_path": str(path), "sha256": sha, "size": 12,
        "updated_at": timestamp,
    }


def _physical(store):
    with store._lock, store._connect() as db:
        return {
            table: db.execute(
                "SELECT * FROM " + table + " ORDER BY 1"
            ).fetchall()
            for table in (
                "placements", "catalog_entries", "source_references",
                "origin_links",
            )
        }


@pytest.mark.parametrize("promotion", [
    "metadata", "scope", "local_state",
])
def test_promoted_catalogue_stops_legacy_physical_replay(tmp_path, promotion):
    store = Storage(tmp_path / "state.sqlite3")
    path = tmp_path / "library" / "not-yet-verified.epub"
    try:
        assert not path.exists()
        store.backup_put(_legacy(path))
        assert store.placements()[0]["state"] == "unverified"
        assert store.catalog_entries()[0]["metadata"] == {
            "imported_from": "backup_items",
        }
        # The catalogue has been independently promoted, while its older
        # unverified placement has not yet been verified by the scanner.
        with store._lock, store._connect() as db:
            if promotion == "metadata":
                db.execute(
                    "UPDATE catalog_entries SET metadata_json=?,title=?",
                    (json.dumps({"scan_confirmed": True}), "Verified book"),
                )
            elif promotion == "scope":
                db.execute(
                    "UPDATE catalog_entries SET identity_id='B',title='Foreign book'",
                )
            else:
                db.execute(
                    "UPDATE catalog_entries SET local_state='missing',title='Missing book'",
                )
        before = _physical(store)
        store.backup_put(_legacy(
            path, sha="b" * 64, title="New manifest", timestamp=999,
        ))
        # Compatibility projection is retained and fresh; no byte/rights
        # evidence is inferred from its SHA or title.
        assert store.backup_get("kobo", "credential-A", "edition-A")["title"] == "New manifest"
        assert _physical(store) == before
        assert store.placements()[0]["artifact_id"] == "sha256:" + "a" * 64
        assert store.acquisitions(provider_id="kobo")[0]["artifact_id"] is None
        assert not path.exists()
    finally:
        store.close()


def test_unpromoted_legacy_catalogue_can_still_refresh(tmp_path):
    store = Storage(tmp_path / "state.sqlite3")
    path = tmp_path / "not-yet-verified.epub"
    try:
        store.backup_put(_legacy(path))
        store.backup_put(_legacy(
            path, sha="b" * 64, title="Updated manifest", timestamp=400,
        ))
        assert store.placements()[0]["artifact_id"] == "sha256:" + "b" * 64
        assert store.placements()[0]["state"] == "unverified"
        catalog = store.catalog_entries()[0]
        assert catalog["title"] == "Updated manifest"
        assert catalog["artifact_id"] == "sha256:" + "b" * 64
        assert catalog["metadata"] == {"imported_from": "backup_items"}
        assert store.entitlements()[0]["identity_id"] == "A"
        assert len(store.acquisitions(provider_id="kobo")) == 1
        with store._lock, store._connect() as db:
            assert db.execute("SELECT COUNT(*) FROM origin_links").fetchone()[0] == 0
    finally:
        store.close()


def test_replay_cannot_mutate_canonical_catalogue_via_stale_legacy_metadata(tmp_path):
    """A real Core catalog_entry_put may replace metadata without a scan."""
    store = Storage(tmp_path / "state.sqlite3")
    path = tmp_path / "book.epub"
    try:
        store.backup_put(_legacy(path))
        old = store.catalog_entries()[0]
        store.catalog_entry_put({
            **old, "metadata": {"origin": "local_scan", "isbn": "synthetic"},
            "title": "Catalogued independently",
        })
        before = _physical(store)
        store.backup_put(_legacy(
            path, title="Legacy title", sha="c" * 64, timestamp=200,
        ))
        assert _physical(store) == before
        assert store.catalog_entries()[0]["title"] == "Catalogued independently"
        assert store.backup_get("kobo", "credential-A", "edition-A")["title"] == "Legacy title"
    finally:
        store.close()
