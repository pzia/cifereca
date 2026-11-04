"""P0 #3185: a partial post-B rehash cannot retire pending candidates.

A maintenance marker is only an advisory history of past same-profile SHA
matches; a candidate that becomes corrupt or otherwise unavailable remains
unresolved until supervised retirement/recovery. All files/SQLite synthetic.
"""
import json

import pytest

from cifereca.core.canonical_post_placement import (
    checkpoint_post_placement_duplicates_change,
    verified_post_placement_duplicates,
)
from cifereca.core.convergence_execution import _check_post_placement_duplicates
from tests.test_core_p0_3171_atomic_publication_v14 import (
    BYTES, new_source, publish, register, rows, store, target,
)
from tests.test_core_p0_3719_post_placement_duplicates import _two_accounts


def _three_copies(store, tmp_path):
    root, paths, pubs = _two_accounts(store, tmp_path)
    source, snapshot = new_source(
        "A", provider="seller-d", credential="card-d", account="buyer-d",
    )
    register(store, "A", "seller-d", "card-d", account="buyer-d")
    extra_path = target(tmp_path, "A", "third.pdf")
    extra = publish(store, root, extra_path, source, snapshot)
    return root, paths + (extra_path,), pubs + (extra,)


def _only_checkpoint(storage):
    with storage._lock, storage._connect() as db:
        records = db.execute(
            "SELECT key,value FROM metadata "
            "WHERE key LIKE 'core.canonical-dedup-pending.v1.%'",
        ).fetchall()
    assert len(records) == 1
    return records[0]


def test_partial_rehash_never_drops_corrupt_registered_duplicate(store, tmp_path):
    root, paths, pubs = _three_copies(store, tmp_path)
    one, two, foreign, three = pubs
    initial = rows(store)
    _check_post_placement_duplicates(
        store, library_root=root, placement_id=one.placement_id,
    )
    key, raw = _only_checkpoint(store)
    before_ids = set(json.loads(raw)["placement_ids"])
    assert before_ids == {
        one.placement_id, two.placement_id, three.placement_id,
    }
    # One duplicate remains physically present and indexed, but its SHA
    # has changed: fresh verification sees only the other candidate.
    paths[3].write_bytes(b"synthetic corrupted, unverified candidate")
    verified = verified_post_placement_duplicates(
        store, library_root=root, placement_id=one.placement_id,
    )
    assert verified == (two.placement_id,)
    _check_post_placement_duplicates(
        store, library_root=root, placement_id=one.placement_id,
    )
    assert store.metadata_get(key) == raw
    assert set(json.loads(store.metadata_get(key))["placement_ids"]) == before_ids
    assert store.placement_get(three.placement_id)["state"] == "present"
    assert store.placement_get(foreign.placement_id)["identity_id"] == "B"
    assert paths[0].read_bytes() == paths[1].read_bytes() == BYTES
    assert paths[3].read_bytes() != BYTES
    assert rows(store) == initial


def test_new_verified_candidate_is_added_without_forgetting_unresolved_one(
    store, tmp_path,
):
    root, paths, pubs = _three_copies(store, tmp_path)
    one, two, foreign, three = pubs
    paths[3].write_bytes(b"synthetic temporary corrupt candidate")
    _check_post_placement_duplicates(
        store, library_root=root, placement_id=one.placement_id,
    )
    key, raw = _only_checkpoint(store)
    assert set(json.loads(raw)["placement_ids"]) == {
        one.placement_id, two.placement_id,
    }
    paths[3].write_bytes(BYTES)
    _check_post_placement_duplicates(
        store, library_root=root, placement_id=one.placement_id,
    )
    now = json.loads(store.metadata_get(key))
    assert set(now["placement_ids"]) == {
        one.placement_id, two.placement_id, three.placement_id,
    }
    assert foreign.placement_id not in now["placement_ids"]
    assert rows(store) == (1, 4, 4, 4)


@pytest.mark.parametrize("poisoned", [
    '{"schema":"future-checkpoint"}',
    '{"schema":"canonical.post-placement-dedup-pending.v1",'
    '"schema":"duplicate"}',
])
def test_replayed_sync_cannot_overwrite_opaque_checkpoint(
    store, tmp_path, poisoned,
):
    root, paths, pubs = _three_copies(store, tmp_path)
    one, _two, _foreign, _three = pubs
    _check_post_placement_duplicates(
        store, library_root=root, placement_id=one.placement_id,
    )
    key, _ = _only_checkpoint(store)
    store.metadata_set(key, poisoned)
    before = rows(store)
    _check_post_placement_duplicates(
        store, library_root=root, placement_id=one.placement_id,
    )
    assert store.metadata_get(key) == poisoned
    assert rows(store) == before
    assert all(path.read_bytes() == BYTES for path in paths)
