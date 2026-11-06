"""P0 #3196 W3: manifest omission cannot erase old Kobo byte evidence.

Purely synthetic v0.7 rows and an offline Kobo manifest; no book transfer,
rights minting, provider network, ADEPT invocation or physical file deletion.
A recorded checksum/size is not a B placement or an acquisition permission.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from cifereca.storage import Storage
from tests.test_kobo_backup_fault_isolation import _ready_adept_state, _service


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
            "pruned": 1, "local_preserved": 0,
            "entitlements_inactivated": 0,
        }
        assert storage.backup_get(
            "kobo", "vault", "previously-acquired",
        ) == original
        assert storage.backup_get("kobo", "vault", "empty-unproven") is None
        assert storage.backup_get("kobo", "another", "other-credential") == other
        assert storage.backup_get("other", "vault", "other-provider")
        assert _canonical_snapshot(storage) == canonical

        assert storage.backup_prune_missing("kobo", "vault", set())["pruned"] == 0
        # This fence is Kobo-specific, not a global change to v0.7 projection.
        assert storage.backup_prune_missing("other", "vault", set())["pruned"] == 1
        assert _canonical_snapshot(storage) == canonical
    finally:
        storage.close()


@pytest.mark.parametrize("initial_status", ["complete", "error", "pending"])
def test_manifest_disappearance_then_return_quarantines_orphaned_completion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, initial_status: str,
):
    edition = {
        "product_id": "book-without-locator", "revision_id": "new-remote-revision",
        "title": "Synthetic", "format": "EPUB",
    }
    service, session = _service(tmp_path, [edition])
    monkeypatch.setattr(
        service, "kobo_adept_state", lambda **_kw: _ready_adept_state(),
    )
    monkeypatch.setattr(
        service, "_execute_kobo_acquisition",
        lambda *_a, **_kw: pytest.fail(
            "a historical completed SHA is not a new download permission"
        ),
    )
    try:
        service.storage.backup_put(_legacy(
            "book-without-locator", credential="vault-1",
            status=initial_status, sha256="a" * 64, size=128,
        ))
        before = service.storage.backup_get(
            "kobo", "vault-1", "book-without-locator",
        )
        session._manifest = []
        missing = service.sync_kobo_backup_account("vault-1")
        assert missing["attempted"] == 0
        assert missing["legacy_pruned"] == 0
        retained = service.storage.backup_get(
            "kobo", "vault-1", "book-without-locator",
        )
        assert retained is not None
        assert retained["sha256"] == before["sha256"]
        assert retained["size"] == before["size"]

        session._manifest = [edition]
        returned = service.sync_kobo_backup_account("vault-1")
        assert returned["attempted"] == 0
        assert returned["downloaded"] == 0
        last = service.storage.backup_get(
            "kobo", "vault-1", "book-without-locator",
        )
        assert last["status"] == "reconcile_required"
        assert last["revision_id"] == "old-revision"
        assert last["sha256"] == before["sha256"]
        assert last["size"] == before["size"]
    finally:
        service.close()
