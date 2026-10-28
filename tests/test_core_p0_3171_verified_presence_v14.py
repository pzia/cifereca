"""#3171: real SQLite v14 + local bytes -> conservative OriginLink/Placement evidence.

No provider calls or download rights. Fixtures share only the atomic publisher's
synthetic inputs, never a supplier's private account or filesystem.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from cifereca.core.convergence_verified_presence import plan_existing_verified_presence
from cifereca.storage import Storage
from tests.test_core_p0_3171_atomic_publication_v14 import (
    BYTES, new_source, publish, register, target,
)


@pytest.fixture
def store(tmp_path):
    database = Storage(tmp_path / "local.sqlite")
    try:
        assert database.schema_version() == 14
        yield database
    finally:
        database.close()


def plan(storage, snapshot, tmp_path):
    result = plan_existing_verified_presence(
        storage, snapshot, library_root=tmp_path / "EBooks",
    )
    assert result.read_only is True
    assert result.acquisition_authorized is False
    return result.decisions


def test_verified_provider_origin_satisfies_only_exact_profile_right(store, tmp_path):
    source, snapshot = new_source()
    register(store, "A", "seller", "card-A")
    path = target(tmp_path, "A")
    publish(store, tmp_path / "EBooks", path, source, snapshot)
    before_placements = store.placements()
    for _ in range(2):
        decisions = plan(store, snapshot, tmp_path)
        assert len(decisions) == 1
        assert decisions[0].state == "present_attested"
        assert decisions[0].provider_satisfied is True
        assert decisions[0].placement_ids == (
            store.placement_for_path(path)["placement_id"],
        )
    assert store.placements() == before_placements
    assert store.entitlements() == []


def test_modified_bytes_revoke_verified_presence_without_deleting_right(store, tmp_path):
    source, snapshot = new_source()
    register(store, "A", "seller", "card-A")
    path = target(tmp_path, "A")
    publish(store, tmp_path / "EBooks", path, source, snapshot)
    path.write_bytes(b"%PDF-1.7\nDifferent but valid PDF bytes\n")
    decisions = plan(store, snapshot, tmp_path)
    assert decisions[0].state != "present_attested"
    assert decisions[0].provider_satisfied is False
    assert path.is_file()
    assert store.placements()[0]["state"] == "present"


def test_other_profile_same_sha_cannot_satisfy_right_without_own_origin(store, tmp_path):
    a, remote_a = new_source("A", provider="seller-a", credential="card-A", account="buyer-A")
    b, remote_b = new_source("B", provider="seller-b", credential="card-B", account="buyer-B")
    register(store, "A", "seller-a", "card-A", account="buyer-A")
    register(store, "B", "seller-b", "card-B", account="buyer-B")
    path_a, path_b = target(tmp_path, "A"), target(tmp_path, "B")
    publish(store, tmp_path / "EBooks", path_a, a, remote_a)
    store.placement_put({
        "path": str(path_b), "identity_id": "B", "state": "present",
        "artifact_id": store.placement_for_path(path_a)["artifact_id"],
    })
    assert plan(store, remote_a, tmp_path)[0].state == "present_attested"
    assert plan(store, remote_b, tmp_path)[0].state == "pending"
    assert store.entitlements() == []


def test_forged_other_profile_placement_same_sha_is_not_presence(store, tmp_path):
    source, snapshot = new_source()
    register(store, "A", "seller", "card-A")
    register(store, "B", "another", "card-B")
    local_a, local_b = target(tmp_path, "A"), target(tmp_path, "B")
    publish(store, tmp_path / "EBooks", local_a, source, snapshot)
    source_artifact = store.placement_for_path(local_a)["artifact_id"]
    local_a.unlink()
    # Simulate historically poisoned SQLite: A's identity, B's path.
    store.placement_put({
        "path": str(local_b), "identity_id": "A", "state": "present",
        "artifact_id": source_artifact,
    })
    assert plan(store, snapshot, tmp_path)[0].provider_satisfied is False
    assert local_b.read_bytes() == BYTES


def test_other_branch_same_right_cannot_use_previous_branch_origin(store, tmp_path):
    source, snapshot = new_source(branch="personal_manifest")
    register(store, "A", "seller", "card-A")
    file = target(tmp_path, "A")
    publish(store, tmp_path / "EBooks", file, source, snapshot)
    _, other_branch = new_source(branch="separate-branch")
    assert plan(store, other_branch, tmp_path)[0].state == "pending"


def test_unavailable_or_negative_snapshot_does_not_infer_remote_absence(store, tmp_path):
    source, snapshot = new_source()
    register(store, "A", "seller", "card-A")
    path = target(tmp_path, "A")
    publish(store, tmp_path / "EBooks", path, source, snapshot)
    failed = replace(snapshot, rights=(), status="failed", coverage="unknown")
    assert plan(store, failed, tmp_path) == ()
    inactive = replace(snapshot, rights=(replace(snapshot.rights[0], state="inactive"),))
    decisions = plan(store, inactive, tmp_path)
    assert len(decisions) == 1
    assert decisions[0].provider_satisfied is False


def test_rebound_provider_connection_disables_presence_without_erasing_origin(store, tmp_path):
    source, snapshot = new_source()
    register(store, "A", "seller", "card-A")
    path = target(tmp_path, "A")
    publish(store, tmp_path / "EBooks", path, source, snapshot)
    assert plan(store, snapshot, tmp_path)[0].provider_satisfied
    with store._lock, store._connect() as db:
        db.execute(
            "UPDATE provider_connections SET identity_id='other-identity' "
            "WHERE provider_id='seller' AND credential_id='card-A'"
        )
    decisions = plan(store, snapshot, tmp_path)
    assert decisions[0].provider_satisfied is False
    assert store.placement_for_path(path)["state"] == "present"
    with store._lock, store._connect() as db:
        assert db.execute("SELECT count(*) FROM origin_links").fetchone()[0] == 1


def test_unregistered_or_mismatched_account_does_not_satisfy_stale_placement(store, tmp_path):
    source, snapshot = new_source()
    register(store, "A", "seller", "card-A")
    path = target(tmp_path, "A")
    publish(store, tmp_path / "EBooks", path, source, snapshot)
    with store._lock, store._connect() as db:
        db.execute(
            "UPDATE provider_accounts SET account_id='another-buyer' "
            "WHERE provider_id='seller' AND credential_id='card-A'"
        )
    assert plan(store, snapshot, tmp_path)[0].provider_satisfied is False


def test_resolved_file_path_remains_verifiable_with_symlink_configured_root(store, tmp_path):
    source, snapshot = new_source()
    register(store, "A", "seller", "card-A")
    real = tmp_path / "real-library"
    root_alias = tmp_path / "library-link"
    physical = real / "A" / "book.pdf"
    physical.parent.mkdir(parents=True)
    physical.write_bytes(BYTES)
    root_alias.symlink_to(real, target_is_directory=True)
    publish(store, root_alias, physical, source, snapshot)
    result = plan_existing_verified_presence(
        store, snapshot, library_root=root_alias,
    )
    assert result.decisions[0].provider_satisfied
