"""P0 #3185: a source row without provider proof cannot authorize
relinking a legacy physical path, even when the OriginLink is typed provider.

Two equal-SHA, same-profile B files and a backup_items compatibility pointer
are synthetic. Keep byte integrity, OriginLinks and every source coordinate.
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



from cifereca.core.canonical_dedup_supervisor import supervise_canonical_duplicate
from cifereca.core.canonical_dedup_reference_plan import (
    plan_verified_canonical_reference_migration,
)
from cifereca.locking import InterProcessLock
from tests.test_core_p0_3171_atomic_publication_v14 import BYTES, rows, store
from tests.test_core_p0_3185_canonical_backup_relink import (
    rig, backup, relink, saved,
)


@pytest.mark.parametrize("unattested_kind", ["unverified", "imported_legacy"])
def test_backup_relink_refuses_provider_link_with_unattested_source_reference(
    rig, unattested_kind,
):
    storage, root, paths, pubs, lock_path = rig
    backup(rig)
    before = saved(rig)
    with storage._lock, storage._connect() as db:
        changed = db.execute(
            "UPDATE source_references SET evidence_kind=? "
            "WHERE provider_id='seller-b' AND credential_id='card-b' "
            "AND account_id='buyer-b' AND edition_id='book'",
            (unattested_kind,),
        )
        assert changed.rowcount == 1
        orig_links = db.execute(
            "SELECT origin_link_id,source_reference_id,artifact_id,evidence_ref "
            "FROM origin_links ORDER BY origin_link_id"
        ).fetchall()
    initial = rows(storage)
    preview = plan_verified_canonical_reference_migration(
        storage, library_root=root,
        candidate_placement_id=pubs[1].placement_id,
        keeper_placement_id=pubs[0].placement_id,
    )
    assert preview.status == "blocked"
    assert preview.reason == "unverified_references"
    assert preview.backup_candidates == 0
    assert preview.unverified_references >= 1

    with pytest.raises(ValueError, match="canonical_backup_relink_origin_missing"):
        relink(rig)
    assert saved(rig) == before

    # The real confirmed maintenance Core must not remove B or fabricate
    # supplier proof merely because the two bytes and the SHA still agree.
    with InterProcessLock(lock_path) as lock:
        completed = supervise_canonical_duplicate(
            storage, library_root=root,
            candidate_placement_id=pubs[1].placement_id,
            keeper_placement_id=pubs[0].placement_id,
            action="commit", confirmed=True, shared_lock=lock,
        )
    assert completed.status == "blocked"
    assert completed.reason == "relink_refused"
    assert storage.placement_get(pubs[1].placement_id)["state"] == "present"
    assert rows(storage) == initial
    assert all(path.read_bytes() == BYTES for path in paths)
    with storage._lock, storage._connect() as db:
        assert db.execute(
            "SELECT origin_link_id,source_reference_id,artifact_id,evidence_ref "
            "FROM origin_links ORDER BY origin_link_id"
        ).fetchall() == orig_links
    assert saved(rig) == before
