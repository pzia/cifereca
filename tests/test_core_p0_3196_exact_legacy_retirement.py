"""#3196: retire ONLY a backup already superseded by fresh canonical A/B.

All accounts, bytes and provider responses are synthetic. No Kobo or ADEPT
network, no filesystem deletion and no new rights or placements.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import json
import sqlite3

import pytest

from cifereca.core.legacy_backup_retirement import (
    retire_attested_legacy_kobo_backup,
)
from cifereca.core.legacy_backup_publication_review import (
    review_legacy_backup_publication,
)
from tests.test_core_p0_3171_atomic_publication_v14 import (
    BYTES, new_source, rows,
)
from tests.test_core_p0_3196_legacy_backup_publication_review import legacy_rig


def _snapshot(rig):
    storage, _root, _file, _pub, _stage = rig
    _source, raw = new_source(
        "A", provider="kobo", credential="card-a",
        account="buyer", edition="book",
    )
    observed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    observation = replace(
        raw.rights[0], observed_at=observed_at,
        provider_revision_id="revision-1",
    )
    snap = replace(
        raw, rights=(observation,), status="success", coverage="complete",
        pagination_complete=True, observed_at=observed_at,
        snapshot_ref="fresh-current-provider-inventory",
    )
    # The actual Source Sync has independently committed exactly this
    # provider_response before entering the one-row retirement transaction.
    with storage._lock, storage._connect() as db:
        db.execute(
            "UPDATE source_references SET evidence_ref=?,observed_at=? "
            "WHERE provider_id='kobo' AND credential_id='card-a' "
            "AND edition_id='book'",
            (observation.evidence_ref, observed_at),
        )
    return snap


def _retire(rig, snap, callback=lambda: True):
    storage, root, _file, _pub, _stage = rig
    return retire_attested_legacy_kobo_backup(
        storage, library_root=root, snapshot=snap, item_id="book",
        right_id="purchase", context_current=callback,
    )


def _canonical(storage):
    with storage._lock, storage._connect() as db:
        return {
            table: db.execute("SELECT * FROM " + table + " ORDER BY 1").fetchall()
            for table in (
                "source_references", "origin_links", "artifacts",
                "placements", "acquisition_ledger", "entitlements",
                "catalog_entries",
            )
        }


def test_exact_fresh_published_row_retires_atomically_and_replay_is_idempotent(
    legacy_rig,
):
    storage, root, file, _pub, _stage = legacy_rig
    snap = _snapshot(legacy_rig)
    reviewed = review_legacy_backup_publication(
        storage, library_root=root, identity_id="A",
        credential_id="card-a", item_id="book",
    )
    assert reviewed.status == "verified_existing_publication"
    before = _canonical(storage)
    retired = _retire(legacy_rig, snap)
    assert retired.status == "retired"
    assert retired.changed is True
    assert storage.backup_get("kobo", "card-a", "book") is None
    assert _canonical(storage) == before
    assert file.read_bytes() == BYTES
    assert rows(storage) == (1, 1, 1, 1)
    with storage._lock, storage._connect() as db:
        journals = db.execute(
            "SELECT value FROM metadata WHERE key LIKE "
            "'core.legacy-kobo-retirement.%'",
        ).fetchall()
    assert len(journals) == 1
    audit = json.loads(journals[0][0])
    assert audit["schema"] == "core.legacy-kobo-retirement.v1"
    assert audit["source"] == [
        "A", "kobo", "card-a", "buyer", "purchases",
        "book", "purchase", "purchase",
    ]
    assert audit["legacy_backup"]["revision_id"] == "revision-1"
    assert audit["legacy_backup"]["local_path"] == str(file)
    assert audit["provider_snapshot_ref"] == snap.snapshot_ref

    repeat = _retire(legacy_rig, snap)
    assert repeat.status == "unchanged"
    assert repeat.changed is False
    assert repeat.reason == "legacy_archive_absent"
    with storage._lock, storage._connect() as db:
        assert db.execute(
            "SELECT COUNT(*) FROM metadata WHERE key LIKE "
            "'core.legacy-kobo-retirement.%'"
        ).fetchone()[0] == 1
    assert _canonical(storage) == before


@pytest.mark.parametrize("defect", [
    "revision_changed", "unqualified_revision", "old_snapshot",
    "partial_snapshot", "other_evidence", "source_disabled",
    "other_source", "path_shared", "file_changed", "file_missing",
    "origin_missing", "receipt_missing", "context_changed",
])
def test_incomplete_or_stale_evidence_never_retires_historical_row(
    legacy_rig, defect, tmp_path,
):
    storage, _root, file, _pub, stage = legacy_rig
    snap = _snapshot(legacy_rig)
    context = lambda: True
    if defect == "revision_changed":
        with storage._lock, storage._connect() as db:
            db.execute(
                "UPDATE backup_items SET revision_id='old-revision' "
                "WHERE provider_id='kobo'",
            )
    elif defect == "unqualified_revision":
        with storage._lock, storage._connect() as db:
            db.execute("UPDATE backup_items SET revision_id=NULL")
    elif defect == "old_snapshot":
        stale = "2026-10-28T08:00:00+00:00"
        snap = replace(snap, observed_at=stale,
                       rights=(replace(snap.rights[0], observed_at=stale),))
    elif defect == "partial_snapshot":
        snap = replace(snap, status="partial", coverage="partial",
                       pagination_complete=False, snapshot_ref="")
    elif defect == "other_evidence":
        snap = replace(snap, rights=(
            replace(snap.rights[0], evidence_ref="other"),
        ))
    elif defect == "source_disabled":
        with storage._lock, storage._connect() as db:
            db.execute(
                "UPDATE source_references SET state='inactive' "
                "WHERE provider_id='kobo'",
            )
    elif defect == "other_source":
        with storage._lock, storage._connect() as db:
            db.execute(
                "INSERT INTO source_references("
                "source_reference_id,identity_id,provider_id,credential_id,"
                "account_id,branch_id,edition_id,right_id,reference_kind,"
                "state,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                ("foreign-source", "A", "kobo", "card-a", "other-account",
                 "purchases", "book", "purchase", "purchase",
                 "active", 1, 1),
            )
    elif defect == "path_shared":
        with storage._lock, storage._connect() as db:
            db.execute(
                "INSERT INTO backup_items("
                "provider_id,credential_id,item_id,title,status,"
                "local_path,updated_at) VALUES(?,?,?,?,?,?,?)",
                ("kobo", "other-card", "another", "Old", "pending",
                 str(file), 1),
            )
    elif defect == "file_changed":
        file.write_bytes(BYTES[:-1] + b"X")
    elif defect == "file_missing":
        file.unlink()
    elif defect == "origin_missing":
        with storage._lock, storage._connect() as db:
            db.execute("DELETE FROM origin_links")
    elif defect == "receipt_missing":
        with storage._lock, storage._connect() as db:
            db.execute(
                "DELETE FROM acquisition_ledger WHERE acquisition_id=?",
                (stage,),
            )
    elif defect == "context_changed":
        context = lambda: False
    old = storage.backup_get("kobo", "card-a", "book")
    canonical = _canonical(storage)
    result = _retire(legacy_rig, snap, context)
    assert result.status == "blocked"
    assert result.changed is False
    assert storage.backup_get("kobo", "card-a", "book") == old
    assert _canonical(storage) == canonical
    with storage._lock, storage._connect() as db:
        assert db.execute(
            "SELECT COUNT(*) FROM metadata WHERE key LIKE "
            "'core.legacy-kobo-retirement.%'"
        ).fetchone()[0] == 0


def test_retirement_journal_failure_rolls_back_entire_delete(legacy_rig):
    storage, _root, file, _pub, _stage = legacy_rig
    snap = _snapshot(legacy_rig)
    old = storage.backup_get("kobo", "card-a", "book")
    canonical = _canonical(storage)
    with storage._lock, storage._connect() as db:
        db.execute(
            "CREATE TRIGGER block_retirement BEFORE INSERT ON metadata "
            "WHEN NEW.key LIKE 'core.legacy-kobo-retirement.%' "
            "BEGIN SELECT RAISE(ABORT,'synthetic_no_audit'); END"
        )
    result = _retire(legacy_rig, snap)
    assert result.status == "blocked"
    assert result.reason == "retirement_unavailable"
    assert storage.backup_get("kobo", "card-a", "book") == old
    assert _canonical(storage) == canonical
    assert file.read_bytes() == BYTES
