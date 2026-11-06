"""#3196: live source Sync can retire exact canonical Kobo archives only.

No supplier network, ADEPT, physical relocation or synthetic right minting.
Fixtures have an already independent canonical A/B/receipt and current PDF.
"""
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
import sqlite3

import pytest

from cifereca.core.canonical_sync_stage import observe_canonical_rights_in_sync
from cifereca.models import Account
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


def test_actual_core_rights_stage_uses_its_held_sync_lease_without_second_get(rig):
    storage, root, path, snapshot, lock_path = rig
    account = Account(
        identity_id="A", provider_id="kobo",
        credential_id="card-a", unique_id="buyer",
    )
    reads = []
    session = SimpleNamespace(
        provider_id="kobo", credential_id="card-a",
        connected=True, _closed=False, accounts=[account],
        rights_observation_snapshot=lambda selected: (
            reads.append(selected) or snapshot
        ),
    )
    service = SimpleNamespace(
        storage=storage, accounts=[account],
        _sessions={"kobo:card-a": session},
        _session_key=lambda provider, credential: provider + ":" + credential,
        sync_source_enabled=lambda selected: selected is account,
        library_dir=lambda: root,
    )
    original = _canonical(storage)

    # Read-only callers of the stage are unable to trigger retirement.
    first = observe_canonical_rights_in_sync(service, account)
    assert first["status"] == "success"
    assert storage.backup_get("kobo", "card-a", "book") is not None
    assert reads == [account]
    assert _canonical(storage) == original

    # The already-held source Sync lock is the only admission difference;
    # the Core uses the EXACT same provider observation with no extra GET.
    with InterProcessLock(lock_path) as lease:
        second = observe_canonical_rights_in_sync(
            service, account, retirement_lease=lease,
        )
    assert second["status"] == "success"
    assert reads == [account, account]
    assert storage.backup_get("kobo", "card-a", "book") is None
    assert _canonical(storage) == original
    assert path.read_bytes() == BYTES


def test_unmatched_earlier_legacy_archive_never_starves_exact_retirement(rig):
    storage, _root, _file, _snapshot, lock_path = rig
    # The A snapshot sees only 'book'. Historical rows preceding it in
    # lexical SQLite order must not consume the four-row bounded work queue.
    with storage._lock, storage._connect() as db:
        for item in ("000-history", "001-history", "002-history", "003-history", "004-history"):
            db.execute(
                "INSERT INTO backup_items("
                "provider_id,credential_id,item_id,title,status,revision_id,"
                "identity_id,account_id,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                ("kobo", "card-a", item, "Old", "complete", "old",
                 "A", "buyer", 1),
            )
    snapshot = rig[3]
    # The provider ALSO reports those five old purchases as current. They
    # lack exact OriginLinks and B Placements, however, so none may consume
    # the bounded four-row retirement budget ahead of 'book'.
    extras = tuple(
        replace(
            snapshot.rights[0], edition_id=item, right_id=item,
            provider_revision_id="old",
        )
        for item in ("000-history", "001-history", "002-history",
                     "003-history", "004-history")
    )
    expanded = replace(snapshot, rights=extras + snapshot.rights)
    with InterProcessLock(lock_path) as lease:
        assert _call(rig, lease, snap=expanded) == 1
    assert storage.backup_get("kobo", "card-a", "book") is None
    assert len([
        row for row in storage.backup_list("kobo", "card-a")
        if row["item_id"].endswith("-history")
    ]) == 5


def test_durable_source_off_vetoes_archive_retirement_inside_sql_txn(rig):
    from cifereca.core.source_auto_consent_bridge import _source_id
    storage, _root, path, snapshot, lock_path = rig
    old = storage.backup_get("kobo", "card-a", "book")
    storage.metadata_set("sync.source.enabled." + _source_id(snapshot.scope), "off")
    with InterProcessLock(lock_path) as lease:
        assert _call(rig, lease) == 0
    assert storage.backup_get("kobo", "card-a", "book") == old
    assert path.read_bytes() == BYTES
