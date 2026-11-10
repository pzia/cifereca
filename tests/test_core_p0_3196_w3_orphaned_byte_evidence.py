"""P0 #3196 W3: manifest omission cannot erase old Kobo byte evidence.

Purely synthetic v0.7 rows and an offline Kobo manifest; no book transfer,
rights minting, provider network, ADEPT invocation or physical file deletion.
A recorded checksum/size is not a B placement or an acquisition permission.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from cifereca.storage import Storage


def _legacy(item, *, provider="kobo", credential="vault", status="complete",
            sha256=None, size=None):
    return {
        "provider_id": provider, "credential_id": credential,
        "identity_id": "default", "account_id": "account",
        "item_id": item, "revision_id": "old-revision",
        "title": "Synthetic", "entitlement_type": "owned",
        "status": status, "local_path": None,
        "sha256": sha256, "size": size,
    }


def _canonical_snapshot(storage):
    with storage._lock, storage._connect() as db:
        return tuple(
            tuple(db.execute("SELECT * FROM " + table + " ORDER BY 1").fetchall())
            for table in (
                "entitlements", "acquisition_ledger", "artifacts",
                "placements", "source_references", "origin_links",
            )
        )


@pytest.mark.parametrize("status,digest,size", [
    ("complete", "a" * 64, 512),
    ("ok", "b" * 64, None),
    ("complete", None, 128),
    ("error", "c" * 64, None),
    ("pending", None, 64),
])
def test_orphaned_kobo_byte_evidence_is_readonly_during_prune(
    tmp_path: Path, status: str, digest: str | None, size: int | None,
):
    storage = Storage(tmp_path / "state.sqlite3")
    try:
        storage.backup_put(_legacy(
            "previously-acquired", status=status, sha256=digest, size=size,
        ))
        storage.backup_put(_legacy("empty-unproven", status="pending"))
        storage.backup_put(_legacy(
            "other-provider", provider="other", sha256="d" * 64, size=200,
        ))
        storage.backup_put(_legacy(
            "other-credential", credential="another", sha256="e" * 64,
        ))
        original = storage.backup_get("kobo", "vault", "previously-acquired")
        other = storage.backup_get("kobo", "another", "other-credential")
        canonical = _canonical_snapshot(storage)

        result = storage.backup_prune_missing("kobo", "vault", set())
        assert result == {
            "pruned": 0, "local_preserved": 0,
            "entitlements_inactivated": 0,
        }
        assert storage.backup_get(
            "kobo", "vault", "previously-acquired",
        ) == original
        assert storage.backup_get("kobo", "vault", "empty-unproven") is not None
        assert storage.backup_get("kobo", "another", "other-credential") == other
        assert storage.backup_get("other", "vault", "other-provider")
        assert _canonical_snapshot(storage) == canonical

        assert storage.backup_prune_missing("kobo", "vault", set())["pruned"] == 0
        # This fence is Kobo-specific, not a global change to v0.7 projection.
        assert storage.backup_prune_missing("other", "vault", set())["pruned"] == 1
        assert _canonical_snapshot(storage) == canonical
    finally:
        storage.close()


