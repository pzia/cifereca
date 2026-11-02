"""#3171: exact verified B presence must be isolated from other profiles.

Only synthetic local PDF bytes and SQLite v14; never issues a provider GET,
creates an OriginLink, or changes local files while planning.
"""
from __future__ import annotations

from cifereca.core import convergence_verified_presence as module
from tests.test_core_p0_3171_atomic_publication_v14 import (
    BYTES, new_source, publish, register, target,
)
from tests.test_core_p0_3171_verified_presence_v14 import store


def _setup(store, tmp_path):
    source, snapshot = new_source()
    register(store, "A", "seller", "card-A")
    path = target(tmp_path, "A")
    publish(store, tmp_path / "EBooks", path, source, snapshot)
    return snapshot, path


def _result(store, snapshot, tmp_path):
    return module.plan_existing_verified_presence(
        store, snapshot, library_root=tmp_path / "EBooks",
    )


def test_positive_exact_local_presence_ignores_large_unrelated_profile_registry(
    store, tmp_path, monkeypatch,
):
    snapshot, path = _setup(store, tmp_path)
    for index in range(230):
        store.identity_put({
            "identity_id": f"extra-{index:03d}",
            "label": f"Unrelated {index}",
            "directory_name": f"separate-folder-{index:03d}",
        })
    original = module._verified_copy
    looked_up = []

    def inspected(path_text, **kwargs):
        looked_up.append(kwargs["registered_directories"])
        return original(path_text, **kwargs)

    monkeypatch.setattr(module, "_verified_copy", inspected)
    before = store.placements()
    decision, = _result(store, snapshot, tmp_path).decisions
    assert decision.state == "present_attested"
    assert decision.provider_satisfied
    assert looked_up == [frozenset({"A"})]
    assert store.placements() == before
    assert path.read_bytes() == BYTES


def test_corrupt_shared_physical_directory_does_not_satisfy_same_sha_origin(
    store, tmp_path,
):
    snapshot, path = _setup(store, tmp_path)
    assert _result(store, snapshot, tmp_path).decisions[0].provider_satisfied
    before = store.placements()
    # Current Storage.identity_put rejects duplicate directories. Only this
    # synthetic fixture drops the index to simulate an old/corrupt database.
    with store._lock, store._connect() as db:
        db.execute("DROP INDEX uq_identity_directory_name")
    try:
        store.identity_put({
            "identity_id": "shadow", "label": "Corrupt shadow",
            "directory_name": "A",
        })
        decision, = _result(store, snapshot, tmp_path).decisions
        assert decision.state != "present_attested"
        assert decision.provider_satisfied is False
        assert path.read_bytes() == BYTES
        assert store.placements() == before
    finally:
        with store._lock, store._connect() as db:
            db.execute("DELETE FROM identities WHERE identity_id='shadow'")
            db.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_identity_directory_name "
                "ON identities(directory_name)"
            )
    assert _result(store, snapshot, tmp_path).decisions[0].provider_satisfied


def test_missing_profile_directory_never_attests_a_registered_placement(
    store, tmp_path,
):
    snapshot, path = _setup(store, tmp_path)
    with store._lock, store._connect() as db:
        db.execute("DELETE FROM identities WHERE identity_id='A'")
    decision, = _result(store, snapshot, tmp_path).decisions
    assert decision.state != "present_attested"
    assert decision.provider_satisfied is False
    assert path.read_bytes() == BYTES
