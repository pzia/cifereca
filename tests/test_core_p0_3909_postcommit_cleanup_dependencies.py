"""P0 #3909: post-B cleanup must not delete another SQLite-linked file.

Synthetic Core candidate and v14 SQLite; no supplier network or physical user
data. A committed OriginLink/Placement is already authoritative before cleanup.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from cifereca.core.canonical_promotion import cleanup_published_staging
from cifereca.core.convergence_execution import CompletedDelivery
from tests.test_core_p0_3909_atomic_promotion import rig, _isolate_delivery_lock
from tests.test_core_p0_3909_postcommit_staging_reconciliation import (
    _published_with_deferred_cleanup,
)
from tests.test_core_p0_3171_atomic_publication_v14 import BYTES, SHA


def _checkpoint(db, service, staged):
    with db._lock, db._connect() as sql:
        raw, = sql.execute(
            "SELECT metadata_json FROM acquisition_ledger "
            "WHERE acquisition_id LIKE 'canonical-intake:%'",
        ).fetchone()
    value = json.loads(raw)
    return CompletedDelivery(
        service.source, value["offer_id"], staged, value["sha256"],
        value["receipt"], value["adept_receipt"],
    )


def _cleanup(db, service, root, staged):
    cleanup_published_staging(
        db, source=service.source,
        completed=_checkpoint(db, service, staged), library_root=root,
    )


def test_postcommit_cleanup_removes_unreferenced_attempt(rig, monkeypatch):
    service, db, root, gets, staged = _published_with_deferred_cleanup(
        rig, monkeypatch,
    )
    final = Path(db.placements()[0]["path"])
    _cleanup(db, service, root, staged)
    assert not staged.exists()
    assert not staged.parent.exists()
    assert final.read_bytes() == BYTES
    assert gets == ["signed-right-offer"]


@pytest.mark.parametrize("other_reference", [
    "placement", "backup", "catalogue_metadata", "other_receipt",
])
def test_postcommit_cleanup_preserves_other_operational_path(
    rig, monkeypatch, other_reference,
):
    service, db, root, gets, staged = _published_with_deferred_cleanup(
        rig, monkeypatch,
    )
    final = Path(db.placements()[0]["path"])
    if other_reference == "placement":
        db.placement_put({
            "artifact_id": "sha256:" + SHA, "identity_id": "A",
            "path": str(staged), "provider_id": "manual",
            "source_item_id": "other-import", "state": "present",
            "metadata": {},
        })
    elif other_reference == "backup":
        with db._lock, db._connect() as sql:
            sql.execute(
                "INSERT INTO backup_items(provider_id,credential_id,item_id,"
                "title,local_path,status,updated_at) VALUES(?,?,?,?,?,?,?)",
                ("manual", "other", "other-import", "Other",
                 str(staged), "complete", 1),
            )
    elif other_reference == "catalogue_metadata":
        with db._lock, db._connect() as sql:
            sql.execute(
                "UPDATE catalog_entries SET metadata_json=?",
                (json.dumps({"path": str(staged)}),),
            )
    else:
        with db._lock, db._connect() as sql:
            original, = sql.execute(
                "SELECT metadata_json FROM acquisition_ledger "
                "WHERE acquisition_id LIKE 'canonical-intake:%'",
            ).fetchone()
            data = json.dumps({"recovery": {"path": str(staged)},
                               "raw": json.loads(original)["schema"]})
            sql.execute(
                "INSERT INTO acquisition_ledger("
                "acquisition_id,provider_id,credential_id,account_id,item_id,"
                "identity_id,status,metadata_json,created_at,updated_at) "
                "SELECT 'external-operation',provider_id,credential_id,"
                "account_id,item_id,identity_id,'retryable_error',?,1,1 "
                "FROM acquisition_ledger "
                "WHERE acquisition_id LIKE 'canonical-intake:%'",
                (data,),
            )

    _cleanup(db, service, root, staged)
    assert staged.read_bytes() == BYTES
    assert staged.parent.is_dir()
    assert final.read_bytes() == BYTES
    assert gets == ["signed-right-offer"]


def test_postcommit_cleanup_recovers_after_stale_backup_reference_removed(
    rig, monkeypatch,
):
    service, db, root, gets, staged = _published_with_deferred_cleanup(
        rig, monkeypatch,
    )
    with db._lock, db._connect() as sql:
        sql.execute(
            "INSERT INTO backup_items(provider_id,credential_id,item_id,"
            "title,local_path,status,updated_at) VALUES(?,?,?,?,?,?,?)",
            ("manual", "other", "other-import", "Other", str(staged),
             "complete", 1),
        )
    _cleanup(db, service, root, staged)
    assert staged.exists()
    with db._lock, db._connect() as sql:
        sql.execute(
            "UPDATE backup_items SET local_path=NULL "
            "WHERE provider_id='manual' AND item_id='other-import'",
        )
    _cleanup(db, service, root, staged)
    assert not staged.exists()
    assert Path(db.placements()[0]["path"]).read_bytes() == BYTES
    assert gets == ["signed-right-offer"]



@pytest.mark.parametrize("mutation", ["second_owner", "original_owner_rebound"])
def test_cleanup_rechecks_physical_owner_in_final_sql_transaction(
    rig, monkeypatch, mutation,
):
    """Changes after profile lookup cannot authorize recursive deletion."""
    from cifereca.core import canonical_promotion

    service, storage, root, gets, staged = _published_with_deferred_cleanup(
        rig, monkeypatch,
    )
    completed = _checkpoint(storage, service, staged)
    final = Path(storage.placements()[0]["path"])
    original_placement = dict(storage.placements()[0])
    if mutation == "second_owner":
        storage.identity_put({
            "identity_id": "B", "directory_name": "B", "label": "B",
        })
        with storage._lock, storage._connect() as db:
            db.execute("DROP INDEX uq_identity_directory_name")

    original_parts = canonical_promotion._stage_parts
    once = []

    def split_then_rebind(physical, profile):
        result = original_parts(physical, profile)
        if not once:
            with storage._lock, storage._connect() as db:
                if mutation == "second_owner":
                    db.execute(
                        "UPDATE identities SET directory_name='A' "
                        "WHERE identity_id='B'"
                    )
                else:
                    db.execute(
                        "UPDATE identities SET directory_name='rebound' "
                        "WHERE identity_id='A'"
                    )
            once.append(True)
        return result

    monkeypatch.setattr(canonical_promotion, "_stage_parts", split_then_rebind)
    assert cleanup_published_staging(
        storage, source=service.source, completed=completed,
        library_root=root,
    ) is False
    assert once == [True]
    assert staged.read_bytes() == BYTES
    assert final.read_bytes() == BYTES
    assert storage.placements()[0] == original_placement
    assert gets == ["signed-right-offer"]

    with storage._lock, storage._connect() as db:
        if mutation == "second_owner":
            db.execute("UPDATE identities SET directory_name='B' WHERE identity_id='B'")
        else:
            db.execute("UPDATE identities SET directory_name='A' WHERE identity_id='A'")
    monkeypatch.setattr(canonical_promotion, "_stage_parts", original_parts)
    assert cleanup_published_staging(
        storage, source=service.source, completed=completed,
        library_root=root,
    ) is True
    assert not staged.exists()
    assert final.read_bytes() == BYTES
    assert storage.placements()[0] == original_placement
