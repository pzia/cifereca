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


def test_direct_cleanup_requires_completed_receipt_from_same_exact_right(
    rig, monkeypatch,
):
    """A completed receipt for right B cannot clean right A's staging."""
    from dataclasses import replace
    from cifereca.core.source_convergence import SourceKey

    service, db, root, gets, staged = _published_with_deferred_cleanup(
        rig, monkeypatch,
    )
    legitimate = _checkpoint(db, service, staged)
    before, = db.placements()
    final = Path(before["path"])
    other = SourceKey(
        service.source.scope, service.source.edition_id, "other-right",
        service.source.kind, service.source.branch,
    )
    assert other != service.source
    wrong = replace(legitimate, source=other)

    # Same profile, account, SHA, path and offer, but the receipt itself
    # belongs to another SourceKey. The helper must refuse BEFORE cleanup.
    assert cleanup_published_staging(
        db, source=service.source, completed=wrong, library_root=root,
    ) is False
    assert staged.read_bytes() == final.read_bytes() == BYTES
    assert db.placements() == [before]
    assert gets == ["signed-right-offer"]

    # The original exact receipt can still remove only its own staging.
    assert cleanup_published_staging(
        db, source=service.source, completed=legitimate, library_root=root,
    ) is True
    assert not staged.exists()
    assert final.read_bytes() == BYTES
    assert db.placements() == [before]
    assert gets == ["signed-right-offer"]


@pytest.mark.parametrize("invalid_binding", ["foreign_connection", "disabled"])
def test_direct_staging_cleanup_requires_active_exact_source_borrower(
    rig, monkeypatch, invalid_binding,
):
    """Direct Core cleanup cannot erase a receipt after source rebinding."""
    from tests.test_core_p0_3171_atomic_publication_v14 import register

    service, db, root, gets, staged = _published_with_deferred_cleanup(
        rig, monkeypatch,
    )
    before = db.placements()[0]
    original_receipt = _checkpoint(db, service, staged)
    final = Path(before["path"])
    scope = service.source.scope
    register(db, "A", "foreign-provider", "foreign-card", account="other")
    with db._lock, db._connect() as sql:
        correct, = sql.execute(
            "SELECT connection_id FROM provider_connections "
            "WHERE provider_id=? AND credential_id=? AND identity_id=?",
            (scope.provider_id, scope.credential_id, scope.identity_id),
        ).fetchone()
        foreign, = sql.execute(
            "SELECT connection_id FROM provider_connections "
            "WHERE provider_id='foreign-provider' AND credential_id='foreign-card'",
        ).fetchone()
        if invalid_binding == "disabled":
            sql.execute(
                "UPDATE provider_connections SET enabled=0 "
                "WHERE connection_id=?", (correct,),
            )
        else:
            sql.execute(
                "UPDATE provider_accounts SET connection_id=? "
                "WHERE identity_id=? AND provider_id=? "
                "AND credential_id=? AND account_id=?",
                (foreign, scope.identity_id, scope.provider_id,
                 scope.credential_id, scope.account_id),
            )

    assert cleanup_published_staging(
        db, source=service.source, completed=original_receipt,
        library_root=root,
    ) is False
    assert staged.read_bytes() == BYTES
    assert final.read_bytes() == BYTES
    assert db.placements() == [before]
    assert gets == ["signed-right-offer"]

    with db._lock, db._connect() as sql:
        sql.execute(
            "UPDATE provider_accounts SET connection_id=? "
            "WHERE identity_id=? AND provider_id=? AND credential_id=? "
            "AND account_id=?",
            (correct, scope.identity_id, scope.provider_id,
             scope.credential_id, scope.account_id),
        )
        sql.execute(
            "UPDATE provider_connections SET enabled=1 WHERE connection_id=?",
            (correct,),
        )
    assert cleanup_published_staging(
        db, source=service.source, completed=original_receipt,
        library_root=root,
    ) is True
    assert not staged.exists()
    assert final.read_bytes() == BYTES
    assert db.placements() == [before]
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



@pytest.mark.parametrize("replacement", ["symlink_parent", "regular_parent", "run_inode"])
def test_postcommit_cleanup_never_deletes_rebound_staging_namespace(
    rig, tmp_path, monkeypatch, replacement,
):
    """An ancestor/run swap AFTER the path probe must not delete foreign bytes.

    The identity and acquisition SQLite facts remain unchanged. Only a
    disposable synthetic filesystem is modified, with a foreign sentinel
    having the same opaque run name as the selected original.
    """
    from cifereca.core import canonical_promotion

    service, storage, root, gets, staged = _published_with_deferred_cleanup(
        rig, monkeypatch,
    )
    completed = _checkpoint(storage, service, staged)
    final = Path(storage.placements()[0]["path"])
    original_placement = dict(storage.placements()[0])
    base = staged.parent.parent
    old_run = staged.parent
    foreign = tmp_path / "foreign-library"
    foreign.mkdir()
    changes = []
    original_dependency = canonical_promotion._metadata_staging_dependency
    if replacement == "run_inode":
        saved = base / "detached-original-run"
        replacement_parent = base
    else:
        saved = base.with_name(".canonical-delivery-detached")
        replacement_parent = (
            foreign if replacement == "symlink_parent" else base
        )

    def swap_after_dependency(db, *, table, marker, current_operation=""):
        result = original_dependency(
            db, table=table, marker=marker,
            current_operation=current_operation,
        )
        if table == "acquisition_ledger" and not changes:
            if replacement == "run_inode":
                old_run.rename(saved)
            else:
                base.rename(saved)
                if replacement == "symlink_parent":
                    base.symlink_to(foreign, target_is_directory=True)
                else:
                    base.mkdir(mode=0o700)
            target = replacement_parent / old_run.name
            target.mkdir()
            (target / "foreign.epub").write_bytes(b"NOT A Cifereca acquisition")
            changes.append(target)
        return result

    monkeypatch.setattr(
        canonical_promotion, "_metadata_staging_dependency",
        swap_after_dependency,
    )
    assert canonical_promotion.cleanup_published_staging(
        storage, source=service.source, completed=completed,
        library_root=root,
    ) is False
    assert len(changes) == 1
    assert (changes[0] / "foreign.epub").read_bytes() == (
        b"NOT A Cifereca acquisition"
    )
    saved_staged = saved / staged.name if replacement == "run_inode" else (
        saved / old_run.name / staged.name
    )
    assert saved_staged.read_bytes() == BYTES
    assert final.read_bytes() == BYTES
    assert storage.placements()[0] == original_placement
    assert gets == ["signed-right-offer"]
