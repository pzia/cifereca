"""#3196: legacy backup replay cannot pretend to reverify local bytes.

A v0.7 backup claim creates only a non-authoritative unverified placeholder;
its updated_at must describe a changed physical *claim*, not a harmless
repeated manifest snapshot or catalogue title. No supplier/DRM/network IO.
"""
from __future__ import annotations

from pathlib import Path

from cifereca.storage import Storage


def _claim(path: Path, *, sha: str, updated_at: int, title="Synthetic book"):
    return {
        "provider_id": "opaque-legacy", "credential_id": "card",
        "account_id": "buyer", "identity_id": "A",
        "item_id": "edition", "entitlement_type": "owned",
        "status": "complete", "title": title,
        "local_path": str(path), "sha256": sha,
        "size": 42, "format": "pdf", "updated_at": updated_at,
    }


def _placement(store):
    records = store.placements()
    assert len(records) == 1
    return records[0]


def test_replayed_legacy_receipt_does_not_refresh_physical_placeholder(tmp_path):
    store = Storage(tmp_path / "state.sqlite")
    path = tmp_path / "missing.pdf"
    first_sha, changed_sha = "a" * 64, "b" * 64
    try:
        # The path is deliberately absent: no byte verification happened.
        assert not path.exists()
        store.backup_put(_claim(path, sha=first_sha, updated_at=1000))
        first = _placement(store)
        assert first["state"] == "unverified"
        assert first["last_seen_at"] is None
        assert first["updated_at"] == 1000
        assert first["artifact_id"] == "sha256:" + first_sha

        # A fresh manifest response/title does not constitute a fresh local
        # hash or owner attestation. The legacy receipt can still update its
        # own descriptive metadata without refreshing B's physical record.
        store.backup_put(_claim(
            path, sha=first_sha, updated_at=2000, title="Title correction",
        ))
        same = _placement(store)
        assert same == first
        assert store.backup_get("opaque-legacy", "card", "edition")["title"] == "Title correction"
        assert store.backup_get("opaque-legacy", "card", "edition")["updated_at"] == 2000

        # A genuinely different *unverified claim* can replace the previous
        # claim while retaining its non-authoritative state and profile.
        store.backup_put(_claim(path, sha=changed_sha, updated_at=3000))
        changed = _placement(store)
        assert changed["artifact_id"] == "sha256:" + changed_sha
        assert changed["updated_at"] == 3000
        assert changed["state"] == "unverified"
        assert changed["last_seen_at"] is None
        assert changed["identity_id"] == "A"

        store.backup_put(_claim(path, sha=changed_sha, updated_at=4000))
        assert _placement(store) == changed
        assert not path.exists()
        assert len(store.entitlements()) == 1
        assert all(x["state"] == "active" for x in store.entitlements())
        with store._lock, store._connect() as sql:
            assert sql.execute("SELECT count(*) FROM origin_links").fetchone()[0] == 0
            assert sql.execute("SELECT count(*) FROM source_references").fetchone()[0] == 0
    finally:
        store.close()


def test_legacy_replay_without_sha_keeps_original_unverified_time(tmp_path):
    store = Storage(tmp_path / "state.sqlite")
    path = tmp_path / "unmaterialized.epub"
    try:
        store.backup_put(_claim(path, sha="", updated_at=1234))
        first = _placement(store)
        assert first["artifact_id"] is None
        assert first["updated_at"] == 1234
        store.backup_put(_claim(path, sha="", updated_at=5678))
        assert _placement(store) == first
        assert store.artifacts() == []
    finally:
        store.close()
