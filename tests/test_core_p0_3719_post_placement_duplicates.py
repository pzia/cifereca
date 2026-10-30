"""#3719: no cross-profile inference, no removal before reference-safe dedup."""
import json
import pytest

from cifereca.core.canonical_post_placement import (
    checkpoint_post_placement_duplicates,
    verified_post_placement_duplicates,
)
from tests.test_core_p0_3171_convergence_execution import rig
from tests.test_core_p0_3171_atomic_publication_v14 import (
    BYTES, new_source, publish, register, rows, store, target,
)


def _two_accounts(store, tmp_path):
    root = tmp_path / "EBooks"
    first, a = new_source("A", provider="seller-a", credential="card-a",
                          account="buyer-a")
    second, b = new_source("A", provider="seller-b", credential="card-b",
                           account="buyer-b")
    foreign, c = new_source("B", provider="seller-c", credential="card-c",
                            account="buyer-c")
    register(store, "A", "seller-a", "card-a", account="buyer-a")
    register(store, "A", "seller-b", "card-b", account="buyer-b")
    register(store, "B", "seller-c", "card-c", account="buyer-c")
    p1 = target(tmp_path, "A", "first.pdf")
    p2 = target(tmp_path, "A", "second.pdf")
    p3 = target(tmp_path, "B", "foreign.pdf")
    result = (
        publish(store, root, p1, first, a),
        publish(store, root, p2, second, b),
        publish(store, root, p3, foreign, c),
    )
    return root, (p1, p2, p3), result


def test_postplacement_detects_only_same_profile_verified_copies(store, tmp_path):
    root, paths, publications = _two_accounts(store, tmp_path)
    one, two, foreign = publications
    before = rows(store)
    assert before == (1, 3, 3, 3)
    assert verified_post_placement_duplicates(
        store, library_root=root, placement_id=one.placement_id,
    ) == (two.placement_id,)
    assert verified_post_placement_duplicates(
        store, library_root=root, placement_id=two.placement_id,
    ) == (one.placement_id,)
    assert verified_post_placement_duplicates(
        store, library_root=root, placement_id=foreign.placement_id,
    ) == ()
    assert rows(store) == before
    assert all(path.read_bytes() == BYTES for path in paths)


def test_tamper_symlink_missing_cannot_be_accepted(store, tmp_path):
    root, (p1, p2, _), (one, two, _) = _two_accounts(store, tmp_path)
    p1.write_bytes(BYTES[:-1] + b"X")
    assert verified_post_placement_duplicates(
        store, library_root=root, placement_id=two.placement_id,
    ) == ()
    p1.unlink()
    p1.symlink_to(p2)
    assert verified_post_placement_duplicates(
        store, library_root=root, placement_id=two.placement_id,
    ) == ()
    assert verified_post_placement_duplicates(
        store, library_root=root, placement_id=one.placement_id,
    ) == ()
    assert rows(store) == (1, 3, 3, 3)


def test_no_candidates_avoids_second_full_file_hash(store, tmp_path, monkeypatch):
    from cifereca.catalog import LocalCatalog
    root = tmp_path / "EBooks"
    source, snapshot = new_source()
    register(store, "A", "seller", "card-A")
    path = target(tmp_path, "A")
    publication = publish(store, root, path, source, snapshot)

    def forbid_rehash(*_args, **_kwargs):
        pytest.fail("unique publication must not rehash all its bytes")

    monkeypatch.setattr(LocalCatalog, "sha256", forbid_rehash)
    assert verified_post_placement_duplicates(
        store, library_root=root, placement_id=publication.placement_id,
    ) == ()
    assert path.read_bytes() == BYTES


def test_unknown_and_missing_owner_are_never_proof(store, tmp_path):
    assert verified_post_placement_duplicates(
        store, library_root=tmp_path / "EBooks", placement_id="absent",
    ) == ()


def test_probe_runs_after_commit_and_failure_cannot_unpublish(rig, monkeypatch):
    from cifereca.core import canonical_post_placement
    from tests.test_core_p0_3171_convergence_execution import (
        fake_delivery, run,
    )
    service, account, source, snapshot, root, lock_path = rig
    observed = []

    def failed_probe(storage, *, library_root, placement_id):
        assert storage is service.storage
        assert library_root == root
        assert rows(storage) == (1, 1, 1, 1)
        assert storage.placement_get(placement_id)["identity_id"] == "A"
        observed.append(placement_id)
        raise RuntimeError("synthetic maintenance failure")

    monkeypatch.setattr(canonical_post_placement,
                        "verified_post_placement_duplicates", failed_probe)
    first = run(service, account, snapshot, root, lock_path, {source},
                fake_delivery(service, root))
    assert first[0].status == "downloaded"
    assert len(observed) == 1
    assert rows(service.storage) == (1, 1, 1, 1)
    assert service.calls == 1
    again = run(
        service, account, snapshot, root, lock_path, {source},
        lambda *_: pytest.fail("post-B probe failure cannot force second GET"),
    )
    assert again[0].status == "present_attested"
    assert service.calls == 1


@pytest.mark.parametrize("probe_fails", [False, True])
def test_attested_presence_retries_post_b_probe_without_a_second_get(
    rig, monkeypatch, probe_fails,
):
    from cifereca.core import canonical_post_placement
    from tests.test_core_p0_3171_convergence_execution import fake_delivery, run

    service, account, source, snapshot, root, lock_path = rig
    first = run(
        service, account, snapshot, root, lock_path, {source},
        fake_delivery(service, root),
    )
    assert first[0].status == "downloaded"
    assert rows(service.storage) == (1, 1, 1, 1)
    assert service.calls == 1
    checked = []

    def probe(storage, *, library_root, placement_id):
        assert storage is service.storage and library_root == root
        assert rows(storage) == (1, 1, 1, 1)
        assert storage.placement_get(placement_id)["identity_id"] == "A"
        checked.append(placement_id)
        if probe_fails:
            raise RuntimeError("synthetic deferred maintenance failure")
        return ("synthetic-duplicate-id",)

    monkeypatch.setattr(
        canonical_post_placement, "verified_post_placement_duplicates", probe,
    )
    second = run(
        service, account, snapshot, root, lock_path, {source},
        lambda *_: pytest.fail("verified provider OriginLink must avoid GET"),
    )
    assert second[0].status == "present_attested"
    assert second[0].reason == "exact_verified_origin"
    assert len(checked) == 1
    assert rows(service.storage) == (1, 1, 1, 1)
    assert service.calls == 1


def test_verified_maintenance_checkpoint_is_durable_scoped_and_non_authoritative(
    store, tmp_path,
):
    root, paths, (first, second, foreign) = _two_accounts(store, tmp_path)
    matches = verified_post_placement_duplicates(
        store, library_root=root, placement_id=second.placement_id,
    )
    assert matches == (first.placement_id,)
    before = rows(store)
    key = checkpoint_post_placement_duplicates(
        store, placement_id=second.placement_id,
        duplicate_placement_ids=matches,
    )
    data = store.metadata_get(key)
    saved = json.loads(data)
    assert saved == {
        "schema": "canonical.post-placement-dedup-pending.v1",
        "identity_id": "A", "artifact_id": second.artifact_id,
        "placement_ids": sorted([first.placement_id, second.placement_id]),
    }
    assert "first.pdf" not in data and "second.pdf" not in data
    assert "seller-a" not in data and "seller-b" not in data
    again = checkpoint_post_placement_duplicates(
        store, placement_id=second.placement_id,
        duplicate_placement_ids=matches,
    )
    assert again == key and store.metadata_get(key) == data
    assert rows(store) == before
    assert all(p.read_bytes() == BYTES for p in paths)

    # SHA is global, but a persisted maintenance marker never crosses B's
    # identity boundary, even when those physical bytes are identical.
    with pytest.raises(ValueError, match="canonical_dedup_candidate_changed"):
        checkpoint_post_placement_duplicates(
            store, placement_id=second.placement_id,
            duplicate_placement_ids=(foreign.placement_id,),
        )
    assert store.metadata_get(key) == data
    assert rows(store) == before


def test_checkpoint_stale_candidate_is_rejected_without_relinking(store, tmp_path):
    root, paths, (first, second, _foreign) = _two_accounts(store, tmp_path)
    with store._lock, store._connect() as db:
        db.execute(
            "UPDATE placements SET state='missing' WHERE placement_id=?",
            (first.placement_id,),
        )
    with pytest.raises(ValueError, match="canonical_dedup_candidate_changed"):
        checkpoint_post_placement_duplicates(
            store, placement_id=second.placement_id,
            duplicate_placement_ids=(first.placement_id,),
        )
    assert all(path.exists() for path in paths)
    assert len(store.placements()) == 3


def test_post_b_probe_persists_advisory_checkpoint(store, tmp_path):
    from cifereca.core.convergence_execution import _check_post_placement_duplicates

    root, paths, (first, second, _foreign) = _two_accounts(store, tmp_path)
    with store._lock, store._connect() as db:
        before = db.execute("SELECT count(*) FROM metadata").fetchone()[0]
    _check_post_placement_duplicates(
        store, library_root=root, placement_id=second.placement_id,
    )
    with store._lock, store._connect() as db:
        records = db.execute(
            "SELECT key,value FROM metadata WHERE key LIKE "
            "'core.canonical-dedup-pending.v1.%'",
        ).fetchall()
        after = db.execute("SELECT count(*) FROM metadata").fetchone()[0]
    assert len(records) == 1 and after == before + 1
    saved = json.loads(records[0][1])
    assert saved["placement_ids"] == sorted([first.placement_id, second.placement_id])
    _check_post_placement_duplicates(
        store, library_root=root, placement_id=second.placement_id,
    )
    with store._lock, store._connect() as db:
        assert db.execute("SELECT count(*) FROM metadata").fetchone()[0] == after
    assert rows(store) == (1, 3, 3, 3)
    assert all(path.read_bytes() == BYTES for path in paths)



def test_stale_checkpoint_is_cleared_only_after_last_present_duplicate_exits(
    store, tmp_path,
):
    from cifereca.core.convergence_execution import _check_post_placement_duplicates

    root, paths, (first, second, foreign) = _two_accounts(store, tmp_path)
    _check_post_placement_duplicates(
        store, library_root=root, placement_id=first.placement_id,
    )
    with store._lock, store._connect() as db:
        key = db.execute(
            "SELECT key FROM metadata WHERE key LIKE "
            "'core.canonical-dedup-pending.v1.%'",
        ).fetchone()[0]
        # The second placement has been deindexed, but neither file was
        # removed and the other profile still contains the same SHA.
        db.execute("UPDATE placements SET state='missing' WHERE placement_id=?",
                   (second.placement_id,))
    assert store.metadata_get(key) is not None
    # A deindexed row alone is not evidence of physical cleanup.
    paths[1].unlink()
    _check_post_placement_duplicates(
        store, library_root=root, placement_id=first.placement_id,
    )
    assert store.metadata_get(key) is None
    assert rows(store) == (1, 3, 3, 3)
    assert all(path.read_bytes() == BYTES for path in paths)
    assert store.placement_get(foreign.placement_id)["state"] == "present"


def test_corrupt_present_duplicate_must_not_clear_pending_checkpoint(
    store, tmp_path,
):
    from cifereca.core.convergence_execution import _check_post_placement_duplicates

    root, (first_path, second_path, _foreign_path), (first, second, _) = (
        _two_accounts(store, tmp_path)
    )
    _check_post_placement_duplicates(
        store, library_root=root, placement_id=first.placement_id,
    )
    with store._lock, store._connect() as db:
        key = db.execute(
            "SELECT key FROM metadata WHERE key LIKE "
            "'core.canonical-dedup-pending.v1.%'",
        ).fetchone()[0]
    second_path.write_bytes(b"tampered, unverified physical candidate")
    _check_post_placement_duplicates(
        store, library_root=root, placement_id=first.placement_id,
    )
    assert store.metadata_get(key) is not None
    assert first_path.read_bytes() == BYTES
    assert store.placement_get(second.placement_id)["state"] == "present"


@pytest.mark.parametrize("breakage", ["keeper_corrupt", "keeper_symlink", "marker"])
def test_missing_keeper_proof_or_unknown_marker_does_not_clear_checkpoint(
    store, tmp_path, breakage,
):
    from cifereca.core.convergence_execution import _check_post_placement_duplicates

    root, (first_path, second_path, _), (first, second, _) = (
        _two_accounts(store, tmp_path)
    )
    _check_post_placement_duplicates(
        store, library_root=root, placement_id=first.placement_id,
    )
    with store._lock, store._connect() as db:
        key = db.execute(
            "SELECT key FROM metadata WHERE key LIKE "
            "'core.canonical-dedup-pending.v1.%'",
        ).fetchone()[0]
        db.execute("UPDATE placements SET state='missing' WHERE placement_id=?",
                   (second.placement_id,))
    if breakage == "keeper_corrupt":
        first_path.write_bytes(b"replacement-content")
    elif breakage == "keeper_symlink":
        first_path.unlink()
        first_path.symlink_to(second_path)
    else:
        store.metadata_set(key, '{"schema":"future-checkpoint"}')
    original = store.metadata_get(key)
    _check_post_placement_duplicates(
        store, library_root=root, placement_id=first.placement_id,
    )
    assert store.metadata_get(key) == original


def test_deindexed_but_still_physical_duplicate_keeps_pending_marker(
    store, tmp_path,
):
    from cifereca.core.convergence_execution import _check_post_placement_duplicates

    root, paths, (first, second, _foreign) = _two_accounts(store, tmp_path)
    _check_post_placement_duplicates(
        store, library_root=root, placement_id=first.placement_id,
    )
    with store._lock, store._connect() as db:
        key = db.execute(
            "SELECT key FROM metadata WHERE key LIKE "
            "'core.canonical-dedup-pending.v1.%'",
        ).fetchone()[0]
        db.execute("UPDATE placements SET state='missing' WHERE placement_id=?",
                   (second.placement_id,))
    original = store.metadata_get(key)
    _check_post_placement_duplicates(
        store, library_root=root, placement_id=first.placement_id,
    )
    assert store.metadata_get(key) == original
    assert all(path.read_bytes() == BYTES for path in paths)
