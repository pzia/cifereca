"""#3171: journal completion cannot silently mark missing or divergent receipts."""
from __future__ import annotations

import pytest

from cifereca.core import canonical_recovery as recovery
from cifereca.storage import Storage
from tests.test_core_p0_3171_atomic_publication_v14 import new_source


def test_completed_delivery_transition_is_atomic_and_idempotent(tmp_path, monkeypatch):
    storage = Storage(tmp_path / "canonical.sqlite")
    key, _snapshot = new_source()
    offer = "synthetic-offer"
    artifact = "sha256:" + "a" * 64
    monkeypatch.setattr(recovery, "_lock_ok", lambda lock: True)
    try:
        with pytest.raises(ValueError, match="checkpoint_missing"):
            recovery.mark_completed_delivery_published(
                storage, source=key, offer_id=offer, artifact_id=artifact,
                shared_lock=object(),
            )
        with storage._lock, storage._connect() as db:
            db.execute(
                "INSERT INTO acquisition_ledger("
                "acquisition_id,provider_id,credential_id,account_id,item_id,"
                "identity_id,status,metadata_json,created_at,updated_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?)",
                (recovery._stage_id(key, offer), key.scope.provider_id,
                 key.scope.credential_id, key.scope.account_id, key.edition_id,
                 key.scope.identity_id, recovery._STAGED, "{}", 1, 1),
            )
        recovery.mark_completed_delivery_published(
            storage, source=key, offer_id=offer, artifact_id=artifact,
            shared_lock=object(),
        )
        recovery.mark_completed_delivery_published(
            storage, source=key, offer_id=offer, artifact_id=artifact,
            shared_lock=object(),
        )
        with pytest.raises(ValueError, match="checkpoint_conflict"):
            recovery.mark_completed_delivery_published(
                storage, source=key, offer_id=offer,
                artifact_id="sha256:" + "b" * 64, shared_lock=object(),
            )
        with storage._lock, storage._connect() as db:
            assert db.execute(
                "SELECT status,artifact_id FROM acquisition_ledger "
                "WHERE acquisition_id=?", (recovery._stage_id(key, offer),),
            ).fetchone() == ("complete", artifact)
    finally:
        storage.close()


@pytest.mark.parametrize("artifact", ["sha256:bad", "sha256:" + "Z" * 64])
def test_invalid_artifact_cannot_complete_journal(tmp_path, monkeypatch, artifact):
    storage = Storage(tmp_path / "canonical.sqlite")
    key, _ = new_source()
    monkeypatch.setattr(recovery, "_lock_ok", lambda lock: True)
    try:
        with pytest.raises(ValueError, match="invalid_published_transition"):
            recovery.mark_completed_delivery_published(
                storage, source=key, offer_id="offer", artifact_id=artifact,
                shared_lock=object(),
            )
    finally:
        storage.close()
