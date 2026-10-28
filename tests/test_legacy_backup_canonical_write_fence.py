"""#3196: unverified legacy replay cannot rewrite canonical physical facts."""
from __future__ import annotations

import hashlib

import pytest

from cifereca.core.canonical_publication import publish_completed_acquisition
from cifereca.core.source_convergence import SourceKey
from cifereca.plugin_api.provider_rights_observation import (
    ProviderRightsScope, ProviderRightObservation, ProviderRightsSnapshot,
)
from cifereca.storage import Storage


@pytest.fixture
def published(tmp_path):
    store = Storage(tmp_path / "state.sqlite")
    store.identity_put({"identity_id": "A", "label": "A", "directory_name": "A"})
    connection = store.provider_connection_put({
        "identity_id": "A", "provider_id": "seller", "credential_id": "card",
    })
    store.provider_account_put({
        "identity_id": "A", "provider_id": "seller", "credential_id": "card",
        "connection_id": connection, "account_id": "buyer",
    })
    path = tmp_path / "books" / "A" / "book.pdf"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"%PDF-1.7\nSynthetic verified content\n")
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    scope = ProviderRightsScope("A", "seller", "card", "buyer")
    source = SourceKey(scope, "edition", "right", "purchase", "purchases")
    snapshot = ProviderRightsSnapshot(scope, (ProviderRightObservation(
        "edition", "right", "purchase", "active", "provider_response",
        "observation", "2026-10-28T08:00:00+00:00", "unknown",
    ),), branch="purchases", status="partial", coverage="partial")
    publish_completed_acquisition(
        store, library_root=tmp_path / "books", path=path, source=source,
        snapshot=snapshot, final_sha256=sha, delivery_receipt_ref="completed-delivery",
    )
    yield store, path, sha
    store.close()


def physical_rows(store):
    with store._lock, store._connect() as db:
        return tuple(db.execute("SELECT * FROM " + table).fetchall() for table in (
            "placements", "catalog_entries", "source_references", "origin_links",
        ))


def legacy(path, sha, **changes):
    return dict({
        "provider_id": "seller", "credential_id": "card", "account_id": "buyer",
        "identity_id": "A", "item_id": "edition", "title": "Unverified title",
        "status": "complete", "local_path": str(path), "sha256": sha,
        "size": 999999, "format": "epub", "updated_at": 123,
    }, **changes)


@pytest.mark.parametrize("same_sha", [True, False])
@pytest.mark.parametrize("state", ["present", "missing", "invalid"])
def test_legacy_replay_preserves_published_content_and_history(published, same_sha, state):
    store, path, sha = published
    with store._lock, store._connect() as db:
        db.execute("UPDATE placements SET state=?", (state,))
        original_artifact = db.execute("SELECT * FROM artifacts WHERE sha256=?", (sha,)).fetchone()
    before = physical_rows(store)
    store.backup_put(legacy(path, sha if same_sha else "b" * 64))
    assert physical_rows(store) == before
    with store._lock, store._connect() as db:
        assert db.execute("SELECT * FROM artifacts WHERE sha256=?", (sha,)).fetchone() == original_artifact
    # The legacy attempt remains historical; it cannot claim this placement or
    # promote its unverified file to a completed canonical artifact receipt.
    assert store.acquisitions()[0]["artifact_id"] is None
    assert store.backup_get("seller", "card", "edition") is not None
    assert hashlib.sha256(path.read_bytes()).hexdigest() == sha


def test_legacy_cannot_rewrite_canonical_unverified_placeholder(tmp_path):
    store = Storage(tmp_path / "state.sqlite")
    try:
        path = tmp_path / "book.pdf"
        store.placement_put({
            "path": str(path), "identity_id": "A", "state": "unverified",
            "artifact_id": "sha256:" + "a" * 64, "metadata": {"scan_pending": True},
        })
        before = physical_rows(store)
        store.backup_put(legacy(path, "b" * 64))
        assert physical_rows(store) == before
        assert store.acquisitions()[0]["artifact_id"] is None
    finally:
        store.close()


def test_untouched_legacy_placeholder_still_accepts_legacy_refresh(tmp_path):
    store = Storage(tmp_path / "state.sqlite")
    try:
        path = tmp_path / "book.pdf"
        store.backup_put(legacy(path, "a" * 64))
        store.backup_put(legacy(path, "b" * 64))
        placement = store.placements()[0]
        assert placement["artifact_id"] == "sha256:" + "b" * 64
        assert placement["state"] == "unverified"
        assert placement["last_seen_at"] is None
        assert len(store.catalog_entries()) == 1
    finally:
        store.close()



def test_unverified_legacy_sha_cannot_poison_real_final_artifact_publication(tmp_path):
    """Same-SHA legacy size/format lies must not block final Core bytes."""
    store = Storage(tmp_path / "state.sqlite")
    try:
        store.identity_put({
            "identity_id": "A", "label": "A", "directory_name": "A",
        })
        connection = store.provider_connection_put({
            "identity_id": "A", "provider_id": "seller",
            "credential_id": "card",
        })
        store.provider_account_put({
            "identity_id": "A", "provider_id": "seller",
            "credential_id": "card", "connection_id": connection,
            "account_id": "buyer",
        })
        path = tmp_path / "books" / "A" / "book.pdf"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"%PDF-1.7\nFinal confirmed bytes\n")
        sha = hashlib.sha256(path.read_bytes()).hexdigest()

        # The v0.7 compatibility record claims an impossible EPUB format and
        # size for the real PDF at this path. Its claim is NOT an Artifact.
        store.backup_put(legacy(path, sha))
        artifact_id = "sha256:" + sha
        legacy_artifact = store.artifact_get(artifact_id)
        assert legacy_artifact["metadata"] == {"imported_from": "backup_items"}
        assert legacy_artifact["size"] == 999999
        assert store.backup_get("seller", "card", "edition")["sha256"] == sha
        assert store.placements()[0]["state"] == "unverified"

        scope = ProviderRightsScope("A", "seller", "card", "buyer")
        source = SourceKey(scope, "edition", "right", "purchase", "purchases")
        snapshot = ProviderRightsSnapshot(
            scope, (ProviderRightObservation(
                "edition", "right", "purchase", "active", "provider_response",
                "observed-positive-right", "2026-10-28T08:00:00+00:00",
                "unknown",
            ),), branch="purchases", status="partial", coverage="partial",
        )
        result = publish_completed_acquisition(
            store, library_root=tmp_path / "books", path=path,
            source=source, snapshot=snapshot, final_sha256=sha,
            delivery_receipt_ref="actual-completed-delivery",
        )
        assert result.artifact_id == artifact_id
        real_artifact = store.artifact_get(artifact_id)
        assert real_artifact["size"] == path.stat().st_size
        assert real_artifact["media_format"] == "pdf"
        assert real_artifact["metadata"] == {}
        assert store.placements()[0]["state"] == "present"
        with store._lock, store._connect() as db:
            assert db.execute(
                "SELECT COUNT(*) FROM origin_links WHERE origin_kind='provider'"
            ).fetchone()[0] == 1
            assert db.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0] == 1
    finally:
        store.close()


def test_legacy_sha_without_existing_file_does_not_create_artifact(tmp_path):
    store = Storage(tmp_path / "state.sqlite")
    try:
        target = tmp_path / "nonexistent.epub"
        store.backup_put(legacy(target, "a" * 64))
        assert store.artifact_get("sha256:" + "a" * 64)["metadata"] == {
            "imported_from": "backup_items"
        }
        assert len(store.backup_list("seller")) == 1
        assert store.placements()[0]["state"] == "unverified"
        assert store.acquisitions()[0]["status"] == "complete"
        # Historical receipt state is not a provider OriginLink or bytes.
        with store._lock, store._connect() as db:
            assert db.execute("SELECT COUNT(*) FROM origin_links").fetchone()[0] == 0
    finally:
        store.close()
