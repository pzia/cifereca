"""#3719: no cross-profile inference, no removal before reference-safe dedup."""
import pytest

from cifereca.core.canonical_post_placement import verified_post_placement_duplicates
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
