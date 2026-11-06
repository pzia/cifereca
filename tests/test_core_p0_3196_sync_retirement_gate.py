"""#3196: live source Sync can retire exact canonical Kobo archives only.

No supplier network, ADEPT, physical relocation or synthetic right minting.
Fixtures have an already independent canonical A/B/receipt and current PDF.
"""
from __future__ import annotations

from dataclasses import replace
import sqlite3

import pytest

from cifereca.core.legacy_backup_retirement_sync import (
    retire_canonical_kobo_legacy_in_sync,
)
from cifereca.locking import InterProcessLock
from tests.test_core_p0_3171_atomic_publication_v14 import BYTES, store
from tests.test_core_p0_3196_legacy_backup_publication_review import legacy_rig
from tests.test_core_p0_3196_exact_legacy_retirement import _snapshot, _canonical


@pytest.fixture
def rig(legacy_rig, monkeypatch, tmp_path):
    storage, root, file, _pub, _stage = legacy_rig
    observation = _snapshot(legacy_rig)
    lock_path = tmp_path / "sync.lock"
    monkeypatch.setattr(
        "cifereca.core.legacy_backup_retirement_sync.acquisition_lock_file",
        lambda: lock_path,
    )
    return storage, root, file, observation, lock_path


def _call(rig, lease, *, snap=None, current=lambda: True):
    storage, root, _file, snapshot, _lock_path = rig
    return retire_canonical_kobo_legacy_in_sync(
        storage, snapshot if snap is None else snap,
        library_root=root, lease=lease, context_current=current,
    )


def test_one_source_sync_lease_retires_only_exact_published_legacy_row(rig):
    storage, _root, path, snapshot, lock_path = rig
    previous = _canonical(storage)
    with InterProcessLock(lock_path) as lease:
        assert _call(rig, lease) == 1
        assert _call(rig, lease) == 0
    assert path.read_bytes() == BYTES
    assert storage.backup_get("kobo", "card-a", "book") is None
    assert _canonical(storage) == previous


def test_no_live_shared_lease_never_enters_sqlite_retirement(rig):
    storage, _root, _file, _snapshot, lock_path = rig
    old = storage.backup_get("kobo", "card-a", "book")
    assert _call(rig, None) == 0
    unused = InterProcessLock(lock_path)
    assert _call(rig, unused) == 0
    with InterProcessLock(lock_path) as lease:
        assert _call(rig, lease, current=lambda: False) == 0
    assert _call(rig, lease) == 0
    assert storage.backup_get("kobo", "card-a", "book") == old


@pytest.mark.parametrize("invalid", ["incomplete", "no_revision", "not_kobo"])
def test_incomplete_provider_observation_cannot_prune_legacy(rig, invalid):
    storage, root, path, snap, lock_path = rig
    old = storage.backup_get("kobo", "card-a", "book")
    if invalid == "incomplete":
        snap = replace(snap, status="partial", coverage="partial",
                       pagination_complete=False, snapshot_ref="")
    elif invalid == "no_revision":
        snap = replace(snap, rights=(replace(
            snap.rights[0], provider_revision_id=None,
        ),))
    elif invalid == "not_kobo":
        from cifereca.plugin_api.provider_rights_observation import ProviderRightsScope
        snap = replace(snap, scope=ProviderRightsScope(
            "A", "other", "card-a", "buyer",
        ))
    with InterProcessLock(lock_path) as lease:
        assert _call(rig, lease, snap=snap) == 0
    assert storage.backup_get("kobo", "card-a", "book") == old
    assert path.read_bytes() == BYTES


def test_unchanged_legacy_evidence_after_sql_audit_failure(rig):
    storage, _root, _path, _snap, lock_path = rig
    old = storage.backup_get("kobo", "card-a", "book")
    with storage._lock, storage._connect() as db:
        db.execute(
            "CREATE TRIGGER deny_retirement BEFORE INSERT ON metadata "
            "WHEN NEW.key LIKE 'core.legacy-kobo-retirement.%' BEGIN "
            "SELECT RAISE(ABORT,'synthetic_audit_error'); END"
        )
    with InterProcessLock(lock_path) as lease:
        assert _call(rig, lease) == 0
    assert storage.backup_get("kobo", "card-a", "book") == old
