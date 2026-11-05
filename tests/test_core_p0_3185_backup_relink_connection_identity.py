"""#3185: legacy backup relink needs the exact enabled connection_id.

SQLite may retain same textual borrower/provider/credential coordinates while
its account row is rebound to another provider_connection. The initial preview
must not display that row as attested; supervised commit must not unlink B.
All bytes, identities and OriginLinks are disposable and offline.
"""
import pytest
@pytest.fixture(autouse=True)
def _enable_legacy_dedup_test_path(monkeypatch):
    """Retain synthetic tests of the OLD Core retirement engine only.

    Production is suspended by #4496. This file exercises pre-existing
    SQLite/FS crash-recovery invariants under an explicit test-only monkeypatch.
    New acceptance tests of the default safety hold never use this fixture.
    """
    from cifereca.core import canonical_dedup_supervisor
    monkeypatch.setattr(canonical_dedup_supervisor, "_ordinary_cleanup_enabled",
                        lambda: True)



from cifereca.core.canonical_dedup_reference_plan import (
    plan_verified_canonical_reference_migration,
)
from cifereca.core.canonical_dedup_supervisor import supervise_canonical_duplicate
from cifereca.locking import InterProcessLock
from tests.test_core_p0_3171_atomic_publication_v14 import BYTES, rows, store
from tests.test_core_p0_3185_canonical_backup_relink import rig, backup, relink, saved


def test_borrower_with_wrong_connection_id_cannot_authorize_backup_relink(rig):
    storage, root, paths, pubs, lock_path = rig
    backup(rig)
    before = saved(rig)
    initial = rows(storage)
    with storage._lock, storage._connect() as db:
        correct = db.execute(
            "SELECT connection_id FROM provider_connections "
            "WHERE provider_id='seller-b' AND credential_id='card-b'"
        ).fetchone()[0]
        other = db.execute(
            "SELECT connection_id FROM provider_connections "
            "WHERE provider_id='seller-a' AND credential_id='card-a'"
        ).fetchone()[0]
        assert correct != other
        affected = db.execute(
            "UPDATE provider_accounts SET connection_id=? "
            "WHERE identity_id='A' AND provider_id='seller-b' "
            "AND credential_id='card-b' AND account_id='buyer-b'",
            (other,),
        )
        assert affected.rowcount == 1
        origins = db.execute(
            "SELECT origin_link_id,identity_id,artifact_id,evidence_ref "
            "FROM origin_links ORDER BY origin_link_id"
        ).fetchall()

    def plan():
        return plan_verified_canonical_reference_migration(
            storage, library_root=root,
            candidate_placement_id=pubs[1].placement_id,
            keeper_placement_id=pubs[0].placement_id,
        )

    reviewed = plan()
    assert reviewed.status == "blocked"
    assert reviewed.reason == "unverified_references"
    assert reviewed.backup_candidates == 0
    assert reviewed.unverified_references >= 1
    with pytest.raises(ValueError, match="canonical_backup_relink_account_rebound"):
        relink(rig)
    with InterProcessLock(lock_path) as lock:
        outcome = supervise_canonical_duplicate(
            storage, library_root=root,
            candidate_placement_id=pubs[1].placement_id,
            keeper_placement_id=pubs[0].placement_id,
            action="commit", confirmed=True, shared_lock=lock,
        )
    assert outcome.status == "blocked"
    assert outcome.reason == "relink_refused"
    assert saved(rig) == before
    assert rows(storage) == initial
    assert all(path.read_bytes() == BYTES for path in paths)
    assert storage.placement_get(pubs[1].placement_id)["state"] == "present"
    with storage._lock, storage._connect() as db:
        assert db.execute(
            "SELECT origin_link_id,identity_id,artifact_id,evidence_ref "
            "FROM origin_links ORDER BY origin_link_id"
        ).fetchall() == origins
        # Repairing the link should restore the legitimate bounded path,
        # without changing the historical source, right, receipt or SHA.
        fixed = db.execute(
            "UPDATE provider_accounts SET connection_id=? "
            "WHERE identity_id='A' AND provider_id='seller-b' "
            "AND credential_id='card-b' AND account_id='buyer-b'",
            (correct,),
        )
        assert fixed.rowcount == 1
    assert plan().status == "review_required"
    assert plan().backup_candidates == 1
    assert relink(rig) == 1
    assert saved(rig)[0] == str(paths[0])
    assert rows(storage) == initial
    assert all(path.read_bytes() == BYTES for path in paths)
