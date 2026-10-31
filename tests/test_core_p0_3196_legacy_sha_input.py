"""#3196: historical 0.7 SHA strings are never unverified canonical artifact IDs."""
from __future__ import annotations

import pytest

from cifereca.storage import Storage


def _legacy(sha, path=None):
    return {
        "provider_id": "kobo", "credential_id": "vault-synthetic",
        "account_id": "buyer", "identity_id": "alice",
        "item_id": "edition", "entitlement_type": "owned",
        "status": "complete", "sha256": sha,
        "local_path": str(path) if path else None,
        "size": 99, "format": "epub", "title": "Synthetic historical",
    }


@pytest.mark.parametrize("bad_sha", [
    "x" * 64, "a" * 63, "a" * 65, "sha256:" + "a" * 64,
    "0" * 63 + "g", "https://example.invalid/signed?sig=private",
    "  " + "a" * 20 + "  ", "ø" * 64,
])
def test_invalid_legacy_digest_is_retained_only_in_backup_history(
    tmp_path, bad_sha,
):
    store = Storage(tmp_path / "history.sqlite3")
    try:
        path = tmp_path / "old.epub"
        path.write_bytes(b"legacy bytes are NOT an attested OriginLink")
        store.backup_put(_legacy(bad_sha, path))
        backup = store.backup_get("kobo", "vault-synthetic", "edition")
        assert backup["sha256"] == bad_sha  # no destructive history rewrite
        assert store.artifacts() == []
        receipt = store.acquisitions(provider_id="kobo")
        assert len(receipt) == 1
        assert receipt[0]["artifact_id"] is None
        placements = store.placements()
        assert len(placements) == 1
        assert placements[0]["artifact_id"] is None
        assert placements[0]["state"] == "unverified"
        with store._lock, store._connect() as db:
            assert db.execute("SELECT COUNT(*) FROM origin_links").fetchone()[0] == 0
            assert db.execute("SELECT COUNT(*) FROM source_references").fetchone()[0] == 0
        assert path.read_bytes() == b"legacy bytes are NOT an attested OriginLink"
    finally:
        store.close()


@pytest.mark.parametrize("valid_sha,canonical", [
    ("a" * 64, "a" * 64),
    ("B" * 64, "b" * 64),
    (" " + "c" * 64 + " ", "c" * 64),
])
def test_compatible_exact_legacy_digest_remains_unverified_only(
    tmp_path, valid_sha, canonical,
):
    store = Storage(tmp_path / "history.sqlite3")
    try:
        store.backup_put(_legacy(valid_sha))
        artifact = store.artifact_get("sha256:" + canonical)
        assert artifact is not None
        assert artifact["metadata"] == {"imported_from": "backup_items"}
        assert store.acquisitions(provider_id="kobo")[0]["artifact_id"] == (
            "sha256:" + canonical
        )
        assert store.backup_get("kobo", "vault-synthetic", "edition")["sha256"] == valid_sha
        with store._lock, store._connect() as db:
            assert db.execute("SELECT COUNT(*) FROM origin_links").fetchone()[0] == 0
    finally:
        store.close()
