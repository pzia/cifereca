"""Private association survives reopen, never becomes a public relation or grant."""
from contextlib import contextmanager
from dataclasses import replace

import pytest

from cifereca.core import notice_c_job_journal as journal
from cifereca.core.notice_c_purchase_execution import PurchaseIntentExecution
from cifereca.core.notice_c_purchase_publication import publish_purchase_public_txn
from cifereca.core.notice_c_purchase_association import (
    _PREFIX, record_purchase_association_txn, read_purchase_notice_association,
)
from cifereca.core.notice_c_cover_index import persist_exact_public_search_cover
from cifereca.core.canonical_sync_stage import persist_attested_source_references
from cifereca.storage import Storage
from tests.test_core_p0_1072_durable_purchase_intent import prepare, set_policy, request
from tests.test_core_p0_1072_nondestructive_purchase_publication import observation


@contextmanager
def allowed():
    yield True


@pytest.mark.parametrize("mode", ["reopen", "rollback", "changed_a", "changed_candidate"])
def test_exact_scoped_receipt_and_c_source(tmp_path, mode):
    path = tmp_path / "association.sqlite"
    db = Storage(path)
    execution = PurchaseIntentExecution(db, clock=lambda: 100)
    try:
        snapshot, candidate = prepare(db)
        other, foreign = prepare(db, owner="B", credential="card-b", account="buyer-b")
        persist_attested_source_references(db, snapshot)
        persist_attested_source_references(db, other)
        set_policy(db, "true")
        ref, = request(db, snapshot, candidate)
        assert execution.attest(
            ref, snapshot, candidate, hold_context=allowed,
            execute=lambda *_: pytest.fail("not needed"), ttl_seconds=30,
        )
        lease = execution.claim(ref, now=100, lease_seconds=30)
        assert lease is not None
        spec = journal.read_job(db, ref).spec
        incoming = observation(candidate, at=100)

        def commit():
            with db._lock, db._connect() as sql:
                sql.execute("BEGIN IMMEDIATE")
                publication = publish_purchase_public_txn(
                    sql, spec, snapshot, candidate, incoming, now=101,
                )
                record_purchase_association_txn(
                    sql, lease, snapshot, candidate, publication, observed_at=100, now=101,
                )
                journal._complete_job_txn(sql, lease, now=101)
                if mode == "rollback":
                    raise ValueError("test rollback")
                return publication
        if mode == "rollback":
            with pytest.raises(ValueError, match="test rollback"):
                commit()
            assert journal.read_job(db, ref).state == "pending"
            with db._lock, db._connect() as sql:
                assert sql.execute("SELECT count(*) FROM metadata WHERE key LIKE ?",
                                   (_PREFIX+"%",)).fetchone() == (0,)
            assert read_purchase_notice_association(db, candidate.source, identity_id="A") is None
            return

        publication = commit()
        assert read_purchase_notice_association(db, candidate.source, identity_id="A") is None
        persist_exact_public_search_cover(db, publication.source)
        with db._lock, db._connect() as sql:
            raw, = sql.execute("SELECT value FROM metadata WHERE key LIKE ?",
                               (_PREFIX+"%",)).fetchone()
            for private in (candidate.title, candidate.public_locator, "buyer-a", "card-a"):
                assert private not in raw
        assert read_purchase_notice_association(db, candidate.source, identity_id="B") is None
        assert read_purchase_notice_association(db, foreign.source, identity_id="B") is None
        if mode == "changed_a":
            updated = replace(snapshot.rights[0], evidence_ref="changed-evidence")
            persist_attested_source_references(db, replace(snapshot, rights=(updated,)))
        if mode == "changed_candidate":
            changed = replace(candidate, public_locator="https://catalogue.example.org/edition/456")
            request(db, snapshot, changed, now=102)
        set_policy(db, "false")
    finally:
        execution.close()
        db.close()

    with_db = Storage(path)
    try:
        result = read_purchase_notice_association(with_db, candidate.source, identity_id="A")
        if mode == "reopen":
            assert result.document == publication.document and result.observed_at == 100
            assert result.authorizes_acquisition is False
        else:
            assert result is None
        assert with_db.entitlements() == [] and with_db.acquisitions() == [] and with_db.placements() == []
    finally:
        with_db.close()
