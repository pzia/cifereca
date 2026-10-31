"""#3185: exact legacy backup pointer migration without any rights inference."""
import sqlite3

import pytest

from cifereca.core.canonical_dedup_backup_relink import (
    relink_canonical_duplicate_backup_paths,
)
from cifereca.locking import InterProcessLock
from tests.test_core_p0_3171_atomic_publication_v14 import (
    BYTES, SHA, new_source, publish, register, rows, store, target,
)


@pytest.fixture
def rig(store, tmp_path, monkeypatch):
    root = tmp_path / "EBooks"
    specs = (
        ("A", "seller-a", "card-a", "buyer-a", "first.pdf"),
        ("A", "seller-b", "card-b", "buyer-b", "second.pdf"),
        ("B", "seller-c", "card-c", "buyer-c", "foreign.pdf"),
    )
    paths, pubs = [], []
    for identity, provider, credential, account, filename in specs:
        key, snapshot = new_source(
            identity, provider=provider, credential=credential,
            account=account, edition="book",
        )
        register(store, identity, provider, credential, account=account)
        path = target(tmp_path, identity, filename)
        paths.append(path)
        pubs.append(publish(store, root, path, key, snapshot))
    lock_path = tmp_path / "locks" / "acquisition.lock"
    monkeypatch.setattr(
        "cifereca.core.canonical_recovery.acquisition_lock_file",
        lambda: lock_path,
    )
    return store, root, paths, pubs, lock_path


def backup(rig, *, index=1, identity=None, account=None, digest=None,
           size=None, status="complete", item="book"):
    storage, _root, paths, _pubs, _lock = rig
    providers = ("seller-a", "seller-b", "seller-c")
    credentials = ("card-a", "card-b", "card-c")
    identities = ("A", "A", "B")
    accounts = ("buyer-a", "buyer-b", "buyer-c")
    with storage._lock, storage._connect() as db:
        db.execute(
            "INSERT INTO backup_items(provider_id,credential_id,item_id,"
            "title,local_path,status,updated_at,identity_id,account_id,"
            "sha256,size) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (providers[index], credentials[index], item, "Synthetic", str(paths[index]),
             status, 1, identities[index] if identity is None else identity,
             accounts[index] if account is None else account,
             SHA if digest is None else digest,
             len(BYTES) if size is None else size),
        )


def relink(rig, *, keeper=None, candidate=None):
    storage, root, _paths, pubs, lock_path = rig
    with InterProcessLock(lock_path) as lock:
        return relink_canonical_duplicate_backup_paths(
            storage, library_root=root,
            candidate_placement_id=candidate or pubs[1].placement_id,
            keeper_placement_id=keeper or pubs[0].placement_id,
            shared_lock=lock,
        )


def saved(rig, credential="card-b"):
    storage, _root, _paths, _pubs, _lock = rig
    with storage._lock, storage._connect() as db:
        return db.execute(
            "SELECT local_path,identity_id,account_id,status,sha256,size "
            "FROM backup_items WHERE credential_id=?",
            (credential,),
        ).fetchone()


def test_complete_backup_relinks_only_exact_published_source(rig):
    storage, _root, paths, pubs, _lock = rig
    backup(rig)
    before = rows(storage)
    previous = saved(rig)
    assert relink(rig) == 1
    assert relink(rig) == 0
    assert saved(rig) == (str(paths[0]), *previous[1:])
    assert rows(storage) == before == (1, 3, 3, 3)
    assert all(path.read_bytes() == BYTES for path in paths)
    assert storage.placement_get(pubs[1].placement_id)["state"] == "present"


@pytest.mark.parametrize("case", [
    "foreign_profile", "wrong_owner", "wrong_account", "wrong_sha",
    "wrong_size", "in_progress", "unknown_source", "bytes_changed",
])
def test_unattested_backup_cannot_be_moved(rig, case):
    storage, _root, paths, pubs, _lock = rig
    backup(
        rig,
        identity="B" if case == "wrong_owner" else None,
        account="other-account" if case == "wrong_account" else None,
        digest="f" * 64 if case == "wrong_sha" else None,
        size=123 if case == "wrong_size" else None,
        status="downloading" if case == "in_progress" else "complete",
        item="other-item" if case == "unknown_source" else "book",
    )
    before = saved(rig)
    if case == "bytes_changed":
        paths[1].write_bytes(BYTES[:-1] + b"X")
    with pytest.raises(ValueError, match="canonical_backup_relink_"):
        relink(rig, keeper=pubs[2].placement_id
               if case == "foreign_profile" else None)
    assert saved(rig) == before
    assert all(path.exists() for path in paths)


def test_transaction_rolls_back_all_backup_paths_on_sql_error(rig):
    storage, _root, paths, _pubs, _lock = rig
    backup(rig)
    # A second exact source has its own independently published OriginLink.
    backup(rig, index=0)
    with storage._lock, storage._connect() as db:
        # Put two source-proven rows at the candidate path, then reject
        # the second update: both legacy pointers must roll back.
        db.execute(
            "UPDATE backup_items SET local_path=? WHERE credential_id='card-a'",
            (str(paths[1]),),
        )
        db.execute(
            "CREATE TRIGGER deny_second_backup_relink "
            "BEFORE UPDATE OF local_path ON backup_items "
            "WHEN NEW.credential_id='card-b' "
            "BEGIN SELECT RAISE(ABORT,'synthetic_backup_failure'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="synthetic_backup_failure"):
        relink(rig)
    assert saved(rig, "card-a")[0] == str(paths[1])
    assert saved(rig, "card-b")[0] == str(paths[1])
    assert all(path.read_bytes() == BYTES for path in paths)


def test_backup_relink_requires_held_common_lock(rig):
    storage, root, paths, pubs, lock_path = rig
    backup(rig)
    with pytest.raises(ValueError, match="canonical_backup_relink_invalid_request"):
        relink_canonical_duplicate_backup_paths(
            storage, library_root=root,
            candidate_placement_id=pubs[1].placement_id,
            keeper_placement_id=pubs[0].placement_id,
            shared_lock=InterProcessLock(lock_path),
        )
    assert saved(rig)[0] == str(paths[1])
