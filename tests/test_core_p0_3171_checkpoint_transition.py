"""#3171: journal completion cannot silently mark missing or divergent receipts."""
from __future__ import annotations

import pytest

from cifereca.core import canonical_recovery as recovery
from cifereca.core.canonical_publication import publish_completed_acquisition
from cifereca.storage import Storage
from cifereca.core.convergence_execution import CompletedDelivery
from tests.test_core_p0_3171_atomic_publication_v14 import new_source, register, target, SHA


def test_completed_delivery_transition_is_atomic_and_idempotent(tmp_path, monkeypatch):
    storage = Storage(tmp_path / "canonical.sqlite")
    key, snapshot = new_source()
    offer = "synthetic-offer"
    artifact = "sha256:" + SHA
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
        with pytest.raises(ValueError, match="checkpoint_conflict"):
            recovery.mark_completed_delivery_published(
                storage, source=key, offer_id=offer, artifact_id=artifact,
                shared_lock=object(),
            )
        with storage._lock, storage._connect() as db:
            assert db.execute(
                "SELECT status,artifact_id FROM acquisition_ledger WHERE acquisition_id=?",
                (recovery._stage_id(key, offer),),
            ).fetchone() == (recovery._STAGED, None)
        register(storage, key.scope.identity_id, key.scope.provider_id,
                 key.scope.credential_id, account=key.scope.account_id)
        path = target(tmp_path, key.scope.identity_id)
        recovery.record_completed_delivery_for_recovery(
            storage, source=key, offer_id=offer,
            completed=CompletedDelivery(key, offer, path, SHA, "synthetic-receipt"),
            library_root=tmp_path / "EBooks", shared_lock=object(),
        )
        with pytest.raises(ValueError, match="published_origin_unverified"):
            recovery.mark_completed_delivery_published(
                storage, source=key, offer_id=offer, artifact_id=artifact,
                shared_lock=object(),
            )
        publish_completed_acquisition(
            storage, library_root=tmp_path / "EBooks", path=path,
            source=key, snapshot=snapshot, final_sha256=SHA,
            delivery_receipt_ref="synthetic-receipt",
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


@pytest.mark.parametrize("case", ["complete", "staged", "wrong_sha", "wrong_receipt",
                                  "legacy_call", "foreign_owner", "rollback"])
def test_relocate_completed_checkpoint_preserves_canonical_history(tmp_path, monkeypatch, case):
    import json
    import os

    root = tmp_path / "EBooks"
    storage = Storage(tmp_path / "canonical.sqlite")
    key, snapshot = new_source()
    owner = key.scope.identity_id
    offer = "synthetic-offer"
    artifact = "sha256:" + SHA
    monkeypatch.setattr(recovery, "_lock_ok", lambda lock: True)
    try:
        register(storage, owner, key.scope.provider_id, key.scope.credential_id,
                 account=key.scope.account_id)
        old = target(tmp_path, owner)
        new = root / ("other" if case == "foreign_owner" else owner) / "Livres" / old.name
        new.parent.mkdir(parents=True)
        record_id = recovery.record_completed_delivery_for_recovery(
            storage, source=key, offer_id=offer,
            completed=CompletedDelivery(key, offer, old, SHA, "synthetic-receipt"),
            library_root=root, shared_lock=object(),
        )
        published = publish_completed_acquisition(
            storage, library_root=root, path=old, source=key, snapshot=snapshot,
            final_sha256=SHA, delivery_receipt_ref="synthetic-receipt",
        )
        if case != "staged":
            recovery.mark_completed_delivery_published(
                storage, source=key, offer_id=offer, artifact_id=artifact,
                shared_lock=object(),
            )
        os.link(old, new)
        with storage._lock, storage._connect() as db:
            if case in ("wrong_sha", "wrong_receipt"):
                raw = recovery._checkpoint(db, record_id)[7]
                data = json.loads(raw)
                if case == "wrong_sha":
                    data["sha256"] = "b" * 64
                else:
                    data["receipt"] = "unrelated-receipt"
                db.execute("UPDATE acquisition_ledger SET metadata_json=? WHERE acquisition_id=?",
                           (json.dumps(data), record_id))
            if case == "rollback":
                db.execute(
                    "CREATE TRIGGER fail_checkpoint_relocation BEFORE UPDATE OF metadata_json "
                    "ON acquisition_ledger BEGIN SELECT RAISE(ABORT, 'fixture_rollback'); END"
                )
            checkpoint_before = recovery._checkpoint(db, record_id)
            history = {table: db.execute("SELECT * FROM " + table).fetchall()
                       for table in ("origin_links", "source_references", "artifacts", "entitlements")}
        baseline = storage.placement_get(published.placement_id)
        kwargs = {} if case == "legacy_call" else {"classification_root": root}
        if case == "complete":
            storage.relocate_local_path(old, new, identity_id=owner, provider_id=None, **kwargs)
            moved = storage.placement_get(published.placement_id)
            assert moved["path"] == str(new)
            assert moved["provider_id"] is None and moved["source_item_id"] is None
            assert moved["artifact_id"] == artifact and moved["identity_id"] == owner
            assert storage.placement_for_path(old) is None
            old.unlink()
            recovered = recovery.recover_completed_delivery(
                storage, source=key, offer_id=offer, library_root=root, shared_lock=object())
            assert recovered is not None and recovered.path == new
            assert recovered.final_sha256 == SHA and recovered.receipt_ref == "synthetic-receipt"
            recovery.mark_completed_delivery_published(
                storage, source=key, offer_id=offer, artifact_id=artifact, shared_lock=object())
        else:
            import sqlite3
            with pytest.raises((ValueError, sqlite3.IntegrityError), match="relocate_|fixture_rollback"):
                storage.relocate_local_path(old, new, identity_id=owner, provider_id=None, **kwargs)
            assert storage.placement_get(published.placement_id) == baseline
            assert storage.placement_for_path(new) is None and old.samefile(new)
        with storage._lock, storage._connect() as db:
            after = recovery._checkpoint(db, record_id)
            assert {table: db.execute("SELECT * FROM " + table).fetchall()
                    for table in history} == history
            if case == "complete":
                assert after[:7] == checkpoint_before[:7]
                expected = json.loads(checkpoint_before[7])
                expected["path"] = str(new)
                assert json.loads(after[7]) == expected
            else:
                assert after == checkpoint_before
    finally:
        storage.close()
