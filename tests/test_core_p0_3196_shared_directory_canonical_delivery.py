"""P0 #3196/#3171: never publish or stage a private book in a shared folder.

A migrated/corrupt SQLite registry may contain overlapping directory_name
values despite the unique index in the current schema. This fixture removes
that index ONLY in the temporary test database. No provider network or ADEPT.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from cifereca.catalog import LocalCatalog
from cifereca.core.canonical_selected_delivery import complete_selected_delivery
from cifereca.locking import InterProcessLock
from tests.test_core_p0_3171_atomic_publication_v14 import (
    BYTES, new_source, publish, register, rows, target,
)
from tests.test_core_p0_3171_atomic_publication_v14 import store
from tests.test_core_p0_3171_canonical_sync_stage import rig


def corrupt_shared_directory(storage):
    storage.identity_put({
        "identity_id": "B", "directory_name": "B", "label": "B",
    })
    with storage._lock, storage._connect() as db:
        db.execute("DROP INDEX uq_identity_directory_name")
        db.execute(
            "UPDATE identities SET directory_name='A' WHERE identity_id='B'"
        )


@pytest.mark.parametrize("corruption_timing", ["before_hash", "after_hash"])
def test_final_publication_refuses_shared_profile_directory(
    store, tmp_path, monkeypatch, corruption_timing,
):
    key, snapshot = new_source()
    register(store, "A", "seller", "card-A")
    file = target(tmp_path, "A")
    root = tmp_path / "EBooks"
    if corruption_timing == "before_hash":
        corrupt_shared_directory(store)
    else:
        store.identity_put({
            "identity_id": "B", "directory_name": "B", "label": "B",
        })
        with store._lock, store._connect() as db:
            db.execute("DROP INDEX uq_identity_directory_name")
        original_hash = LocalCatalog.sha256
        tampered = [False]

        def hash_then_alias(path):
            result = original_hash(path)
            if not tampered[0]:
                tampered[0] = True
                with store._lock, store._connect() as db:
                    db.execute(
                        "UPDATE identities SET directory_name='A' "
                        "WHERE identity_id='B'"
                    )
            return result

        monkeypatch.setattr(LocalCatalog, "sha256", staticmethod(hash_then_alias))
    with pytest.raises(ValueError, match="canonical_profile_directory_ambiguous"):
        publish(store, root, file, key, snapshot)
    assert rows(store) == (0, 0, 0, 0)
    assert file.read_bytes() == BYTES
    with store._lock, store._connect() as db:
        db.execute("UPDATE identities SET directory_name='B' WHERE identity_id='B'")
    assert publish(store, root, file, key, snapshot).changed
    assert rows(store) == (1, 1, 1, 1)


def test_selected_delivery_never_calls_provider_in_ambiguous_profile(
    rig, monkeypatch,
):
    service, storage, root = rig
    corrupt_shared_directory(storage)
    offered = replace(
        service.snapshot.rights[0],
        delivery="available", delivery_offer_id="synthetic-offer",
    )
    monkeypatch.setattr(
        "cifereca.core.canonical_selected_delivery.acquisition_lock_file",
        lambda: root.parent / "state" / "acquisition.lock",
    )
    service.session.rights_selected_delivery_candidate = (
        lambda *_a, **_kw: pytest.fail("no provider GET in shared profile")
    )
    with InterProcessLock(root.parent / "state" / "acquisition.lock") as lease:
        with pytest.raises(
            ValueError, match="canonical_delivery_profile_directory_ambiguous",
        ):
            complete_selected_delivery(
                service, service.account, service.session, service.source,
                offered, library_root=root, shared_lock=lease,
            )
    assert not (root / "A" / ".canonical-delivery").exists()
    assert rows(storage) == (0, 0, 0, 0)


def test_shared_directory_created_during_get_revokes_staging_receipt(
    rig, monkeypatch,
):
    service, storage, root = rig
    storage.identity_put({
        "identity_id": "B", "directory_name": "B", "label": "B",
    })
    with storage._lock, storage._connect() as db:
        db.execute("DROP INDEX uq_identity_directory_name")
    offered = replace(
        service.snapshot.rights[0],
        delivery="available", delivery_offer_id="synthetic-offer",
    )
    monkeypatch.setattr(
        "cifereca.core.canonical_selected_delivery.acquisition_lock_file",
        lambda: root.parent / "state" / "acquisition.lock",
    )
    calls = []

    def candidate(_account, *, target_dir, **_kwargs):
        calls.append("GET")
        candidate_file = Path(target_dir) / "received.pdf"
        candidate_file.write_bytes(BYTES)
        with storage._lock, storage._connect() as db:
            db.execute(
                "UPDATE identities SET directory_name='A' "
                "WHERE identity_id='B'"
            )
        return candidate_file, ""

    service.session.rights_selected_delivery_candidate = candidate
    with InterProcessLock(root.parent / "state" / "acquisition.lock") as lease:
        with pytest.raises(
            ValueError, match="canonical_delivery_profile_directory_ambiguous",
        ):
            complete_selected_delivery(
                service, service.account, service.session, service.source,
                offered, library_root=root, shared_lock=lease,
            )
    assert calls == ["GET"]
    assert (root / "A" / ".canonical-delivery").is_dir()
    assert not list((root / "A" / ".canonical-delivery").iterdir())
    assert rows(storage) == (0, 0, 0, 0)



def test_staging_promotion_and_cleanup_share_the_exact_directory_fence(
    store, tmp_path,
):
    from cifereca.core.canonical_promotion import _profile

    source, _snapshot = new_source()
    register(store, "A", "seller", "card-A")
    root = tmp_path / "EBooks"
    (root / "A").mkdir(parents=True)
    corrupt_shared_directory(store)
    with pytest.raises(ValueError, match="canonical_promotion_profile_ambiguous"):
        _profile(store, root, source)
    with store._lock, store._connect() as db:
        db.execute("UPDATE identities SET directory_name='B' WHERE identity_id='B'")
    canonical_root, profile = _profile(store, root, source)
    assert canonical_root == root
    assert profile == root / "A"
