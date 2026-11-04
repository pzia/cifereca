"""P0 #3909: maintenance after committed B, not a second provider delivery.

Synthetic v14 SQLite + real source Sync, with fault injection between
publication, operation-journal finalization and physical staging cleanup.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from cifereca.core import convergence_execution
from cifereca.core.sync_coordination import synchronize_account_source
from tests.test_core_p0_3909_atomic_promotion import (
    rig, _isolate_delivery_lock, _drive,
)
from tests.test_core_p0_3171_atomic_publication_v14 import BYTES, SHA


@pytest.fixture(autouse=True)
def _unexpected_postcommit_exception_must_fail_test(monkeypatch, tmp_path):
    # All Core acquisition modules must see the same synthetic process lock.
    # The production path is shared; tests patch independent module imports.
    monkeypatch.setattr(
        "cifereca.core.canonical_postcommit_reconcile.acquisition_lock_file",
        lambda: tmp_path / "state" / "acquisition.lock",
    )
    # The production caller deliberately protects an already committed B
    # from maintenance errors. In these controlled new scenarios, however,
    # all input is known: expose unexpected exceptions as test failures.
    original = convergence_execution.reconcile_committed_staging

    def checked(*args, **kwargs):
        try:
            return original(*args, **kwargs)
        except Exception as exc:
            pytest.fail(
                "unexpected_postcommit_exception:"
                + type(exc).__name__ + ":" + str(exc),
            )

    monkeypatch.setattr(
        convergence_execution, "reconcile_committed_staging", checked,
    )


def _published_with_deferred_cleanup(rig, monkeypatch):
    service, db, root, gets = _drive(rig)

    def refused(*args, **kwargs):
        raise OSError("SECRET PRIVATE URL should not escape logs")

    with monkeypatch.context() as patch:
        patch.setattr(convergence_execution, "cleanup_published_staging", refused)
        result = synchronize_account_source(service, service.account)
    assert result["canonical_execution"]["downloaded"] == 1
    assert gets == ["signed-right-offer"]
    final, = db.placements()
    assert Path(final["path"]).parent == root / "A" / "Acquisitions"
    assert Path(final["path"]).read_bytes() == BYTES
    staged, = (root / "A" / ".canonical-delivery").glob("run-*/received.pdf")
    assert staged.read_bytes() == BYTES
    with db._lock, db._connect() as sql:
        assert sql.execute(
            "SELECT status FROM acquisition_ledger "
            "WHERE acquisition_id LIKE 'canonical-intake:%'",
        ).fetchone() == ("complete",)
        assert sql.execute(
            "SELECT COUNT(*) FROM origin_links WHERE origin_kind='provider'",
        ).fetchone()[0] == 1
    return service, db, root, gets, staged


@pytest.mark.parametrize("proof_loss", ["before_cleanup", "after_final_hash"])
def test_postcommit_cleanup_refuses_stale_source_without_provider_proof(
    rig, monkeypatch, proof_loss,
):
    """A valid receipt and SHA cannot override a missing/mid-flight A proof."""
    from cifereca.core import canonical_postcommit_reconcile as postcommit
    from cifereca.locking import InterProcessLock

    service, db, root, gets, staged = _published_with_deferred_cleanup(
        rig, monkeypatch,
    )
    before, = db.placements()
    final = Path(before["path"])
    assert staged.read_bytes() == final.read_bytes() == BYTES
    with db._lock, db._connect() as sql:
        source_ref, observed = sql.execute(
            "SELECT source_reference_id,evidence_ref FROM source_references "
            "WHERE identity_id=? AND provider_id=?",
            (service.source.scope.identity_id, service.source.scope.provider_id),
        ).fetchone()
        assert observed
        if proof_loss == "before_cleanup":
            sql.execute(
                "UPDATE source_references SET evidence_ref='' "
                "WHERE source_reference_id=?", (source_ref,),
            )

    # The provider proof can also be invalidated AFTER initial SQL lookup and
    # physical hash, before the last guarded SQL check. Neither race may
    # authorize deleting the only historical staged receipt copy.
    triggered = []
    if proof_loss == "after_final_hash":
        original_hash = postcommit._path_verified

        def invalidate_after_final_hash(*args, **kwargs):
            verified = original_hash(*args, **kwargs)
            if kwargs.get("path") == final and not triggered:
                with db._lock, db._connect() as sql:
                    sql.execute(
                        "UPDATE source_references SET evidence_ref='' "
                        "WHERE source_reference_id=?", (source_ref,),
                    )
                triggered.append(True)
            return verified

        monkeypatch.setattr(postcommit, "_path_verified",
                            invalidate_after_final_hash)

    lock_path = root.parent / "state" / "acquisition.lock"
    with InterProcessLock(lock_path) as lock:
        result = postcommit.reconcile_committed_staging(
            db, source=service.source, placement_id=before["placement_id"],
            library_root=root, shared_lock=lock, current=lambda: True,
        )
    assert result is False
    assert triggered == ([True] if proof_loss == "after_final_hash" else [])
    assert staged.read_bytes() == final.read_bytes() == BYTES
    assert db.placements() == [before]
    assert gets == ["signed-right-offer"]

    with db._lock, db._connect() as sql:
        sql.execute(
            "UPDATE source_references SET evidence_ref=? "
            "WHERE source_reference_id=?", (observed, source_ref),
        )
    with InterProcessLock(lock_path) as lock:
        repaired = postcommit.reconcile_committed_staging(
            db, source=service.source, placement_id=before["placement_id"],
            library_root=root, shared_lock=lock, current=lambda: True,
        )
    assert repaired is True
    assert not staged.exists()
    assert final.read_bytes() == BYTES
    assert db.placements() == [before]
    assert gets == ["signed-right-offer"]


@pytest.mark.parametrize("mutation", [
    "other_connection_before", "disabled_before", "other_connection_after_hash",
])
def test_postcommit_cleanup_requires_current_exact_borrower(
    rig, monkeypatch, mutation,
):
    """A persisted receipt cannot clean staging for a rebound borrower."""
    from cifereca.core import canonical_postcommit_reconcile as postcommit
    from cifereca.locking import InterProcessLock
    from tests.test_core_p0_3171_atomic_publication_v14 import register

    service, db, root, gets, staged = _published_with_deferred_cleanup(
        rig, monkeypatch,
    )
    before, = db.placements()
    final = Path(before["path"])
    source = service.source
    scope = source.scope
    register(db, "A", "unrelated-provider", "unrelated-card", account="other")
    with db._lock, db._connect() as sql:
        correct, = sql.execute(
            "SELECT connection_id FROM provider_connections "
            "WHERE identity_id=? AND provider_id=? AND credential_id=?",
            (scope.identity_id, scope.provider_id, scope.credential_id),
        ).fetchone()
        unrelated, = sql.execute(
            "SELECT connection_id FROM provider_connections "
            "WHERE identity_id='A' AND provider_id='unrelated-provider' "
            "AND credential_id='unrelated-card'",
        ).fetchone()
    assert correct != unrelated

    changed = []
    def corrupt():
        with db._lock, db._connect() as sql:
            if mutation == "disabled_before":
                sql.execute(
                    "UPDATE provider_connections SET enabled=0 WHERE connection_id=?",
                    (correct,),
                )
            else:
                sql.execute(
                    "UPDATE provider_accounts SET connection_id=? "
                    "WHERE identity_id=? AND provider_id=? "
                    "AND credential_id=? AND account_id=?",
                    (unrelated, scope.identity_id, scope.provider_id,
                     scope.credential_id, scope.account_id),
                )
        changed.append(True)

    if mutation != "other_connection_after_hash":
        corrupt()
    else:
        original_hash = postcommit._path_verified

        def invalidate_after_hash(*args, **kwargs):
            verified = original_hash(*args, **kwargs)
            if kwargs.get("path") == final and not changed:
                corrupt()
            return verified

        monkeypatch.setattr(postcommit, "_path_verified", invalidate_after_hash)

    lock_path = root.parent / "state" / "acquisition.lock"
    with InterProcessLock(lock_path) as lock:
        result = postcommit.reconcile_committed_staging(
            db, source=source, placement_id=before["placement_id"],
            library_root=root, shared_lock=lock, current=lambda: True,
        )
    assert result is False
    assert changed == [True]
    assert staged.read_bytes() == final.read_bytes() == BYTES
    assert db.placements() == [before]
    assert gets == ["signed-right-offer"]

    # Restoring the exact live connection enables the *same* receipt cleanup,
    # with neither a second remote GET nor a new OriginLink/Placement.
    with db._lock, db._connect() as sql:
        sql.execute(
            "UPDATE provider_accounts SET connection_id=? "
            "WHERE identity_id=? AND provider_id=? "
            "AND credential_id=? AND account_id=?",
            (correct, scope.identity_id, scope.provider_id,
             scope.credential_id, scope.account_id),
        )
        sql.execute(
            "UPDATE provider_connections SET enabled=1 WHERE connection_id=?",
            (correct,),
        )
    with InterProcessLock(lock_path) as lock:
        resumed = postcommit.reconcile_committed_staging(
            db, source=source, placement_id=before["placement_id"],
            library_root=root, shared_lock=lock, current=lambda: True,
        )
    assert resumed is True
    assert not staged.exists()
    assert final.read_bytes() == BYTES
    assert db.placements() == [before]
    assert gets == ["signed-right-offer"]


def test_postcommit_cleanup_refuses_unbounded_exact_provider_origin_claims(
    rig, monkeypatch,
):
    """A 513-link import cannot authorize selecting one receipt for unlink."""
    from cifereca.core import canonical_postcommit_reconcile as postcommit
    from cifereca.locking import InterProcessLock

    service, db, root, gets, staged = _published_with_deferred_cleanup(
        rig, monkeypatch,
    )
    before, = db.placements()
    final = Path(before["path"])
    with db._lock, db._connect() as sql:
        origin = sql.execute(
            "SELECT identity_id,artifact_id,source_reference_id,"
            "created_at,updated_at FROM origin_links "
            "WHERE origin_kind='provider' LIMIT 1",
        ).fetchone()
        assert origin
        sql.executemany(
            "INSERT INTO origin_links(origin_link_id,origin_kind,"
            "identity_id,artifact_id,source_reference_id,evidence_ref,"
            "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
            [
                (f"postcommit-untrusted-claim-{i}", "provider",
                 origin[0], origin[1], origin[2],
                 f"not-the-core-delivery-receipt-{i}", origin[3], origin[4])
                for i in range(512)
            ],
        )

    lock_path = root.parent / "state" / "acquisition.lock"
    with InterProcessLock(lock_path) as lock:
        refused = postcommit.reconcile_committed_staging(
            db, source=service.source, placement_id=before["placement_id"],
            library_root=root, shared_lock=lock, current=lambda: True,
        )
    assert refused is False
    assert staged.read_bytes() == final.read_bytes() == BYTES
    assert db.placements() == [before]
    assert gets == ["signed-right-offer"]

    with db._lock, db._connect() as sql:
        sql.execute(
            "DELETE FROM origin_links "
            "WHERE origin_link_id LIKE 'postcommit-untrusted-claim-%'",
        )
    with InterProcessLock(lock_path) as lock:
        repaired = postcommit.reconcile_committed_staging(
            db, source=service.source, placement_id=before["placement_id"],
            library_root=root, shared_lock=lock, current=lambda: True,
        )
    assert repaired is True
    assert not staged.exists()
    assert final.read_bytes() == BYTES
    assert db.placements() == [before]
    assert gets == ["signed-right-offer"]


def test_next_sync_cleans_post_B_staging_without_get_or_new_placement(
    rig, monkeypatch,
):
    service, db, root, gets, staged = _published_with_deferred_cleanup(rig, monkeypatch)
    before, = db.placements()
    result = synchronize_account_source(service, service.account)
    assert result["canonical_execution"]["present_attested"] == 1
    assert result["canonical_execution"]["downloaded"] == 0
    assert gets == ["signed-right-offer"]
    assert not staged.exists()
    assert db.placements() == [before]
    assert Path(before["path"]).read_bytes() == BYTES
    assert list((root / "A" / ".canonical-delivery").glob("run-*")) == []
    third = synchronize_account_source(service, service.account)
    assert third["canonical_execution"]["present_attested"] == 1
    assert gets == ["signed-right-offer"]


def test_next_sync_finalizes_journal_lost_after_B_and_cleanup(rig, monkeypatch):
    service, db, root, gets = _drive(rig)

    def failed_flag(*args, **kwargs):
        raise RuntimeError("PRIVATE JOURNAL DATA")

    with monkeypatch.context() as patch:
        patch.setattr(
            convergence_execution, "mark_completed_delivery_published", failed_flag,
        )
        first = synchronize_account_source(service, service.account)
    assert first["canonical_execution"]["downloaded"] == 1
    assert not list((root / "A" / ".canonical-delivery").glob("run-*"))
    with db._lock, db._connect() as sql:
        assert sql.execute(
            "SELECT status,artifact_id FROM acquisition_ledger "
            "WHERE acquisition_id LIKE 'canonical-intake:%'",
        ).fetchone() == ("canonical_receipt_staged", None)
    second = synchronize_account_source(service, service.account)
    assert second["canonical_execution"]["present_attested"] == 1
    assert gets == ["signed-right-offer"]
    with db._lock, db._connect() as sql:
        assert sql.execute(
            "SELECT status,artifact_id FROM acquisition_ledger "
            "WHERE acquisition_id LIKE 'canonical-intake:%'",
        ).fetchone() == ("complete", "sha256:" + SHA)
    assert len(db.placements()) == 1


@pytest.mark.parametrize("fault", ["forged_receipt", "wrong_source", "symlink", "different_sha"])
def test_ambiguous_post_B_bytes_cannot_be_erased_or_adopted(
    rig, monkeypatch, fault,
):
    service, db, root, gets, staged = _published_with_deferred_cleanup(rig, monkeypatch)
    before, = db.placements()
    if fault == "symlink":
        outside = root.parent / "not-owned.pdf"
        outside.write_bytes(BYTES)
        staged.unlink()
        staged.symlink_to(outside)
    else:
        with db._lock, db._connect() as sql:
            key, raw = sql.execute(
                "SELECT acquisition_id,metadata_json FROM acquisition_ledger "
                "WHERE acquisition_id LIKE 'canonical-intake:%'",
            ).fetchone()
            checkpoint = json.loads(raw)
            if fault == "forged_receipt":
                checkpoint["receipt"] = "core-delivery:" + "f" * 64
            elif fault == "wrong_source":
                checkpoint["source"][3] = "OTHER ACCOUNT"
            else:
                checkpoint["sha256"] = "e" * 64
            sql.execute(
                "UPDATE acquisition_ledger SET metadata_json=? WHERE acquisition_id=?",
                (json.dumps(checkpoint), key),
            )
    second = synchronize_account_source(service, service.account)
    assert second["canonical_execution"]["present_attested"] == 1
    assert gets == ["signed-right-offer"]
    assert staged.exists() or staged.is_symlink()
    if fault == "symlink":
        assert staged.is_symlink()
        assert outside.read_bytes() == BYTES
    else:
        assert staged.read_bytes() == BYTES
    assert db.placements() == [before]
    assert Path(before["path"]).read_bytes() == BYTES


def test_without_live_right_consent_does_not_clean_published_staging(
    rig, monkeypatch,
):
    service, db, root, gets, staged = _published_with_deferred_cleanup(rig, monkeypatch)
    service._canonical_persisted_consent = lambda _: frozenset()
    second = synchronize_account_source(service, service.account)
    assert gets == ["signed-right-offer"]
    assert second["canonical_execution"]["downloaded"] == 0
    assert staged.read_bytes() == BYTES
    assert len(db.placements()) == 1
