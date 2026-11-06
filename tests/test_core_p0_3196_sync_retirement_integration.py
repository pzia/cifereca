"""#3196 W1/W2: installed canonical Sync retires ONLY an exact superseded archive.

These tests use real v14 SQLite, physical bytes and the same Core acquisition
lock, but synthetic observations and delivery receipts. Never a Kobo GET.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import json

import pytest

from cifereca.models import Account
from tests.test_core_p0_3171_atomic_publication_v14 import (
    BYTES, SHA, new_source, register, rows, store,
)
from tests.test_core_p0_3171_convergence_execution import (
    FakeService, fake_delivery, run,
)
from tests.test_core_p0_3196_exact_legacy_retirement import _snapshot
from tests.test_core_p0_3196_legacy_backup_publication_review import legacy_rig


def _sync(legacy_rig, snapshot, tmp_path, monkeypatch):
    storage, root, _file, _published, _stage = legacy_rig
    account = Account(
        identity_id="A", provider_id="kobo",
        credential_id="card-a", unique_id="buyer",
    )
    service = FakeService(storage, [account])
    source, _ = new_source(
        "A", provider="kobo", credential="card-a",
        account="buyer", edition="book",
    )
    lock_path = tmp_path / "acquisition.lock"
    monkeypatch.setattr(
        "cifereca.core.convergence_execution.acquisition_lock_file",
        lambda: lock_path,
    )
    outcomes = run(
        service, account, snapshot, root, lock_path, {source},
        lambda *_: pytest.fail("already attested B must never do a GET"),
    )
    assert len(outcomes) == 1
    assert outcomes[0].status == "present_attested"
    return storage


def test_sync_retires_previously_published_exact_kobo_archive(
    legacy_rig, tmp_path, monkeypatch,
):
    storage, _root, original, _published, _stage = legacy_rig
    snapshot = _snapshot(legacy_rig)
    before = rows(storage)
    _sync(legacy_rig, snapshot, tmp_path, monkeypatch)

    assert storage.backup_get("kobo", "card-a", "book") is None
    assert rows(storage) == before
    assert original.read_bytes() == BYTES
    with storage._lock, storage._connect() as db:
        audits = db.execute(
            "SELECT value FROM metadata WHERE key LIKE "
            "'core.legacy-kobo-retirement.%'"
        ).fetchall()
    assert len(audits) == 1
    assert json.loads(audits[0][0])["legacy_backup"]["revision_id"] == "revision-1"

    # The next Sync sees only canonical B: no GET, no recreated 0.7 row,
    # no extra journal and no canonical mutation.
    _sync(legacy_rig, snapshot, tmp_path, monkeypatch)
    assert rows(storage) == before
    assert storage.backup_get("kobo", "card-a", "book") is None


@pytest.mark.parametrize("defect", (
    "legacy_revision_changed",
    "legacy_owner_changed",
    "canonical_receipt_missing",
    "snapshot_incomplete",
))
def test_sync_preserves_uncertain_kobo_archives(
    legacy_rig, tmp_path, monkeypatch, defect,
):
    storage, _root, original, _published, stage = legacy_rig
    snapshot = _snapshot(legacy_rig)
    before = rows(storage)
    if defect == "snapshot_incomplete":
        snapshot = replace(
            snapshot, status="partial", coverage="partial",
            pagination_complete=False,
        )
    else:
        with storage._lock, storage._connect() as db:
            if defect == "legacy_revision_changed":
                db.execute(
                    "UPDATE backup_items SET revision_id='former-revision'"
                )
            elif defect == "legacy_owner_changed":
                db.execute("UPDATE backup_items SET account_id='other-buyer'")
            else:
                db.execute(
                    "DELETE FROM acquisition_ledger WHERE acquisition_id=?",
                    (stage,),
                )
    _sync(legacy_rig, snapshot, tmp_path, monkeypatch)
    assert storage.backup_get("kobo", "card-a", "book") is not None
    assert rows(storage) == before
    assert original.read_bytes() == BYTES
    with storage._lock, storage._connect() as db:
        assert db.execute(
            "SELECT COUNT(*) FROM metadata WHERE key LIKE "
            "'core.legacy-kobo-retirement.%'"
        ).fetchone()[0] == 0


def test_sync_retires_only_after_successful_first_canonical_delivery(
    store, tmp_path, monkeypatch,
):
    account = Account(
        identity_id="A", provider_id="kobo",
        credential_id="card-a", unique_id="buyer",
    )
    register(store, "A", "kobo", "card-a", account="buyer")
    source, raw = new_source(
        "A", provider="kobo", credential="card-a",
        account="buyer", edition="book",
    )
    observed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    right = replace(
        raw.rights[0], delivery="available",
        delivery_offer_id="synthetic-selected-offer",
        provider_revision_id="revision-1", observed_at=observed_at,
    )
    snapshot = replace(
        raw, rights=(right,), status="success", coverage="complete",
        pagination_complete=True, observed_at=observed_at,
        snapshot_ref="synthetic-fully-observed-current-inventory",
    )
    root = tmp_path / "EBooks"
    local_path = root / "A" / "book.pdf"
    # A historical 0.7 archive, NOT a new backup_put (the typed Core will
    # fence backup_put after publication).
    with store._lock, store._connect() as db:
        db.execute(
            "INSERT INTO backup_items("
            "provider_id,credential_id,item_id,revision_id,title,status,"
            "local_path,size,sha256,identity_id,account_id,updated_at"
            ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            ("kobo", "card-a", "book", "revision-1", "Historical",
             "complete", str(local_path), len(BYTES), SHA, "A", "buyer", 1),
        )
    service = FakeService(store, [account])
    lock_path = tmp_path / "acquisition.lock"
    monkeypatch.setattr(
        "cifereca.core.convergence_execution.acquisition_lock_file",
        lambda: lock_path,
    )
    monkeypatch.setattr(
        "cifereca.core.canonical_recovery.acquisition_lock_file",
        lambda: lock_path,
    )

    def deliver(key, observation):
        assert store.backup_get("kobo", "card-a", "book") is not None
        return fake_delivery(service, root)(key, observation)

    outcomes = run(
        service, account, snapshot, root, lock_path, {source}, deliver,
    )
    assert len(outcomes) == 1
    assert outcomes[0].status == "downloaded"
    assert store.backup_get("kobo", "card-a", "book") is None
    assert rows(store) == (1, 1, 1, 1)
    assert local_path.read_bytes() == BYTES
    with store._lock, store._connect() as db:
        archives = db.execute(
            "SELECT value FROM metadata WHERE key LIKE "
            "'core.legacy-kobo-retirement.%'"
        ).fetchall()
    assert len(archives) == 1
    assert json.loads(archives[0][0])["legacy_backup"]["sha256"] == SHA
