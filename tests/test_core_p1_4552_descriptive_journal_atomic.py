"""#4552: journal lease and descriptive SQL commit are one transaction."""
from hashlib import sha256

import pytest

from cifereca.storage import Storage
from cifereca.core import notice_c_job_journal as journal
from cifereca.core.notice_c_cover_index import canonical_cover_document_ref
from cifereca.core.notice_descriptive_materialization_store import (
    publish_descriptive_materialization, read_descriptive_materialization,
)
from tests.test_core_p1_4552_descriptive_materialization_store import (
    SELECTION, SOURCE, facts, materialization,
)


def setup(tmp_path):
    storage = Storage(tmp_path / "descriptive.sqlite")
    admitted = facts()
    row = materialization(admitted)
    capture = lambda db: (SELECTION, admitted, SOURCE)
    doc = canonical_cover_document_ref(row.target_edition_id)
    spec = journal.public_job_spec(
        "recompute_aggregate", doc,
        input_digest=sha256(row.execution_key.encode("ascii")).hexdigest(),
        implementation_digest=SOURCE,
    )
    ref = journal.request_job(storage, spec, now=100, expected_generation=0)
    lease = journal.claim_job(storage, ref, now=101, lease_seconds=60)
    assert lease is not None
    return storage, row, capture, ref, lease


def test_atomic_journal_publication_and_replay(tmp_path):
    storage, row, capture, ref, lease = setup(tmp_path)
    publish_descriptive_materialization(
        storage, row, expected_execution_key=None,
        capture_current_txn=capture, journal_lease=lease, journal_now=102,
    )
    assert journal.read_job(storage, ref).state == "current"
    assert read_descriptive_materialization(
        Storage(tmp_path / "descriptive.sqlite"),
        target_edition_id=row.target_edition_id,
        canonical_type=row.canonical_type, scope=row.scope,
        capture_current_txn=capture,
    ) == row
    with pytest.raises(ValueError, match="notice_work_lease_changed"):
        publish_descriptive_materialization(
            storage, row, expected_execution_key=row.execution_key,
            capture_current_txn=capture, journal_lease=lease, journal_now=103,
        )


def test_expired_lease_rolls_back_sql_write(tmp_path):
    storage, row, capture, ref, lease = setup(tmp_path)
    with pytest.raises(ValueError, match="notice_work_lease_changed"):
        publish_descriptive_materialization(
            storage, row, expected_execution_key=None,
            capture_current_txn=capture, journal_lease=lease, journal_now=162,
        )
    assert journal.read_job(storage, ref).state == "pending"
    assert read_descriptive_materialization(
        storage, target_edition_id=row.target_edition_id,
        canonical_type=row.canonical_type, scope=row.scope,
        capture_current_txn=capture,
    ) is None


def test_snapshot_change_rolls_back_job_completion(tmp_path):
    storage, row, capture, ref, lease = setup(tmp_path)
    with pytest.raises(ValueError, match="stale_descriptive_materialization"):
        publish_descriptive_materialization(
            storage, row, expected_execution_key=None,
            capture_current_txn=lambda db: (SELECTION, facts(revision="rev:2"), SOURCE),
            journal_lease=lease, journal_now=102,
        )
    assert journal.read_job(storage, ref).state == "pending"


def test_missing_partial_journal_binding_fails_closed(tmp_path):
    storage, row, capture, _, lease = setup(tmp_path)
    with pytest.raises(ValueError, match="descriptive_journal_binding_required"):
        publish_descriptive_materialization(
            storage, row, expected_execution_key=None,
            capture_current_txn=capture, journal_lease=lease,
        )
