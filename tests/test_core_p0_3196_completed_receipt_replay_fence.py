"""P0 #3196: the old v0.7 writer cannot regress an existing completed B receipt.

Synthetic SQLite v14 only; no Kobo network, ADEPT or user library access.
The canonical stage-id usually differs from v0.7's deterministic ID,
but imported/transition databases may carry a completed B journal at that ID.
"""
import json

import pytest

from cifereca.storage import Storage


def _backup(*, status="pending", sha="a" * 64, account="borrower", owner="A"):
    return {
        "provider_id": "kobo", "credential_id": "synthetic-vault",
        "account_id": account, "identity_id": owner,
        "item_id": "synthetic-book", "revision_id": "v1",
        "title": "Synthetic title", "format": "epub",
        "local_path": "/synthetic/absent.epub", "sha256": sha,
        "size": 73, "status": status, "updated_at": 123,
    }


@pytest.fixture
def storage(tmp_path):
    service = Storage(tmp_path / "state.sqlite")
    try:
        service.backup_put(_backup(status="complete"))
        yield service
    finally:
        service.close()


def _ledger(storage):
    with storage._lock, storage._connect() as db:
        return db.execute(
            "SELECT acquisition_id,provider_id,credential_id,account_id,"
            "item_id,identity_id,status,artifact_id,metadata_json,updated_at "
            "FROM acquisition_ledger WHERE provider_id='kobo'"
        ).fetchone()


def _snapshot(storage):
    with storage._lock, storage._connect() as db:
        return {
            table: db.execute(
                "SELECT * FROM " + table + " ORDER BY rowid"
            ).fetchall()
            for table in ("entitlements", "artifacts", "placements",
                          "catalog_entries", "acquisition_ledger")
        }


def _promote_synthetic_receipt(storage, raw=None):
    receipt = {
        "schema": "canonical.completed-delivery.v1",
        "source": [
            "A", "kobo", "synthetic-vault", "borrower",
            "purchases", "synthetic-book", "purchase", "purchase",
        ],
        "path": "/synthetic/absent.epub", "sha256": "a" * 64,
        "offer_id": "synthetic-offer", "receipt": "synthetic-proof",
        "adept_receipt": "",
    }
    with storage._lock, storage._connect() as db:
        db.execute(
            "UPDATE acquisition_ledger SET status='complete',"
            "metadata_json=?,artifact_id=? WHERE provider_id='kobo'",
            (raw if raw is not None else json.dumps(receipt),
             "sha256:" + "a" * 64),
        )


@pytest.mark.parametrize("status", [
    "pending", "acquiring", "attention_required", "complete",
])
def test_existing_completed_canonical_checkpoint_is_immune_to_legacy_replay(
    storage, status,
):
    _promote_synthetic_receipt(storage)
    before = _snapshot(storage)
    old_receipt = _ledger(storage)
    storage.backup_put(_backup(status=status, sha="b" * 64))
    assert _snapshot(storage) == before
    assert _ledger(storage) == old_receipt
    compat = storage.backup_get("kobo", "synthetic-vault", "synthetic-book")
    assert compat["status"] == status
    assert compat["sha256"] == "b" * 64
    # The old projection's new SHA is not an Artifact or attested B.
    assert storage.artifact_get("sha256:" + "b" * 64) is None


@pytest.mark.parametrize("raw", [
    '{"schema":"canonical.completed-delivery.v1","schema":"legacy"}',
    '{"other":"canonical.completed-delivery.v1"}',
    '{"schema":"canonical.completed-delivery.v1",',
])
def test_ambiguous_canonical_marker_rolls_back_legacy_update(storage, raw):
    _promote_synthetic_receipt(storage, raw)
    before = _snapshot(storage)
    projection = storage.backup_get("kobo", "synthetic-vault", "synthetic-book")
    with pytest.raises(ValueError, match="backup_canonical_receipt_unreadable"):
        storage.backup_put(_backup(status="acquiring", sha="b" * 64))
    assert _snapshot(storage) == before
    assert storage.backup_get("kobo", "synthetic-vault", "synthetic-book") == projection


def test_foreign_owner_on_canonical_checkpoint_is_never_rebound(storage):
    _promote_synthetic_receipt(storage)
    with storage._lock, storage._connect() as db:
        db.execute(
            "UPDATE acquisition_ledger SET identity_id='other-profile' "
            "WHERE provider_id='kobo'"
        )
    before = _snapshot(storage)
    projection = storage.backup_get("kobo", "synthetic-vault", "synthetic-book")
    with pytest.raises(ValueError, match="backup_acquisition_owner_conflict"):
        storage.backup_put(_backup(status="acquiring", sha="b" * 64))
    assert _snapshot(storage) == before
    assert storage.backup_get("kobo", "synthetic-vault", "synthetic-book") == projection


def test_unfinished_compatibility_checkpoint_preserves_existing_writer(storage):
    before = _ledger(storage)
    assert json.loads(before[8]) == {"legacy_status": "complete"}
    storage.backup_put(_backup(status="acquiring"))
    after = _ledger(storage)
    assert after[6] == "acquiring"
    assert json.loads(after[8]) == {"legacy_status": "complete"}
    assert storage.backup_get(
        "kobo", "synthetic-vault", "synthetic-book"
    )["status"] == "acquiring"


def _add_canonical_stage(storage, *, account="borrower", owner="A"):
    receipt = {
        "schema": "canonical.completed-delivery.v1",
        "source": [owner, "kobo", "synthetic-vault", account,
                   "purchases", "synthetic-book", "purchase", "purchase"],
        "offer_id": "synthetic-offer",
        "path": "/synthetic/absent.epub",
        "sha256": "a" * 64,
        "receipt": "synthetic-published-receipt",
        "adept_receipt": "",
    }
    with storage._lock, storage._connect() as db:
        db.execute(
            "INSERT INTO acquisition_ledger(acquisition_id,provider_id,"
            "credential_id,account_id,item_id,identity_id,status,"
            "artifact_id,metadata_json,created_at,updated_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            ("canonical-intake:independent-stage", "kobo",
             "synthetic-vault", account, "synthetic-book", owner,
             "complete", "sha256:" + "a" * 64, json.dumps(receipt), 1, 1),
        )


def test_normal_distinct_canonical_stage_stops_second_legacy_ledger_writer(
    storage,
):
    """The usual canonical-intake stage ID differs from the legacy ID."""
    _add_canonical_stage(storage)
    original = _snapshot(storage)
    with storage._lock, storage._connect() as db:
        count = db.execute(
            "SELECT COUNT(*) FROM acquisition_ledger"
        ).fetchone()[0]
    assert count == 2
    storage.backup_put(_backup(status="acquiring", sha="b" * 64))
    assert _snapshot(storage) == original
    assert storage.backup_get(
        "kobo", "synthetic-vault", "synthetic-book"
    )["status"] == "acquiring"
    assert storage.artifact_get("sha256:" + "b" * 64) is None


def test_first_legacy_projection_after_canonical_stage_remains_non_authoritative(
    tmp_path,
):
    db = Storage(tmp_path / "state.sqlite")
    try:
        _add_canonical_stage(db)
        before = _snapshot(db)
        assert before["entitlements"] == []
        assert before["placements"] == []
        assert len(before["acquisition_ledger"]) == 1
        db.backup_put(_backup(status="complete"))
        assert _snapshot(db) == before
        assert len(db.backup_list("kobo")) == 1
        assert db.artifact_get("sha256:" + "a" * 64) is None
    finally:
        db.close()


def test_other_borrower_canonical_stage_does_not_suppress_legacy_scope(storage):
    # A provider credential can serve multiple distinct borrower accounts.
    # An unrelated canonical stage is not evidence for this local backup.
    _add_canonical_stage(storage, account="another-borrower", owner="B")
    storage.backup_put(_backup(status="acquiring"))
    with storage._lock, storage._connect() as db:
        statuses = db.execute(
            "SELECT account_id,identity_id,status FROM acquisition_ledger "
            "ORDER BY acquisition_id"
        ).fetchall()
    assert ("borrower", "A", "acquiring") in statuses
    assert ("another-borrower", "B", "complete") in statuses


def test_canonical_checkpoint_with_incomplete_status_blocks_legacy_replay(
    storage,
):
    _promote_synthetic_receipt(storage)
    with storage._lock, storage._connect() as db:
        db.execute(
            "UPDATE acquisition_ledger SET status='acquiring' "
            "WHERE provider_id='kobo'"
        )
    before = _snapshot(storage)
    projection = storage.backup_get("kobo", "synthetic-vault", "synthetic-book")
    with pytest.raises(ValueError, match="backup_canonical_receipt_status_conflict"):
        storage.backup_put(_backup(status="complete", sha="b" * 64))
    assert _snapshot(storage) == before
    assert storage.backup_get("kobo", "synthetic-vault", "synthetic-book") == projection


@pytest.mark.parametrize("escape", ["schema_value", "schema_key", "both"])
def test_json_escaped_completed_checkpoint_still_blocks_legacy_replay(
    storage, escape,
):
    # A JSON serializer/import may escape the schema key, marker, or both.
    # Literal-substring detection alone missed valid canonical checkpoints.
    raw_key = r"\u0073chema" if escape in ("schema_key", "both") else "schema"
    raw_marker = (
        r"\u0063anonical.completed-delivery.v1"
        if escape in ("schema_value", "both")
        else "canonical.completed-delivery.v1"
    )
    _promote_synthetic_receipt(
        storage, '{"' + raw_key + '":"' + raw_marker + '"}',
    )
    original = _snapshot(storage)
    storage.backup_put(_backup(status="acquiring", sha="b" * 64))
    assert _snapshot(storage) == original
    assert storage.backup_get(
        "kobo", "synthetic-vault", "synthetic-book"
    )["status"] == "acquiring"
    assert storage.artifact_get("sha256:" + "b" * 64) is None


def test_escaped_duplicate_schema_rolls_back_without_legacy_authority(storage):
    _promote_synthetic_receipt(
        storage,
        r'{"schema":"legacy","\u0073chema":"canonical.completed-delivery.v1"}',
    )
    original = _snapshot(storage)
    projection = storage.backup_get("kobo", "synthetic-vault", "synthetic-book")
    with pytest.raises(ValueError, match="backup_canonical_receipt_unreadable"):
        storage.backup_put(_backup(status="complete", sha="b" * 64))
    assert _snapshot(storage) == original
    assert storage.backup_get(
        "kobo", "synthetic-vault", "synthetic-book"
    ) == projection


def test_other_versioned_schema_does_not_suppress_legacy_recovery(storage):
    # A structurally valid non-canonical schema remains a compatibility row.
    _promote_synthetic_receipt(storage, '{"schema":"legacy.v0.7"}')
    storage.backup_put(_backup(status="acquiring"))
    assert _ledger(storage)[6] == "acquiring"
    assert storage.backup_get(
        "kobo", "synthetic-vault", "synthetic-book"
    )["status"] == "acquiring"
