"""Two typed documentary intents, one existing journal, no new rights."""
from contextlib import closing
from types import SimpleNamespace

import pytest

from cifereca.core import notice_c_job_journal as journal
from cifereca.core.canonical_sync_stage import persist_attested_source_references
from cifereca.core.notice_c_enrichment_intent import (
    CatalogueReparseIntent, ExistingPurchaseIntent, request_notice_enrichment,
)
from cifereca.storage import Storage
from tests.test_core_p0_1072_durable_purchase_intent import prepare, set_policy
from tests.test_core_p0_4982_raw_input_receipt import BODY, public, record
from tests.test_core_p0_4982_reparse_requests import PARSER, TOKEN


def test_two_triggers_same_journal_independent_proofs_and_restart(tmp_path):
    path = tmp_path / "intent.sqlite"
    with closing(Storage(path)) as db:
        snapshot, candidate = prepare(db)
        set_policy(db, "true")
        purchase = ExistingPurchaseIntent(snapshot, (candidate,), lambda: True)
        with pytest.raises(ValueError, match="source_changed"):
            request_notice_enrichment(db, purchase, now=100)
        source = public(db)
        receipt = record(db, source, producer_token=TOKEN)
        catalogue = CatalogueReparseIntent(source, receipt, BODY, PARSER, 0)
        c = request_notice_enrichment(db, catalogue, now=100)
        assert c.changed is None
        assert request_notice_enrichment(db, catalogue, now=100) == c
        persist_attested_source_references(db, snapshot)
        p = request_notice_enrichment(db, purchase, now=100)
        assert p.changed is True
        repeated = request_notice_enrichment(db, purchase, now=100)
        assert repeated.refs == p.refs and repeated.changed is False
        assert p.refs[0] != c.refs[0]
        assert db.entitlements() == [] and db.acquisitions() == [] and db.placements() == []
    with closing(Storage(path)) as db:
        assert journal.read_job(db, p.refs[0]).spec.kind == "fetch_source_notice"
        assert journal.read_job(db, c.refs[0]).spec.kind == "reparse_cached_input"
        for value in ("loan", "import", SimpleNamespace(kind="purchase")):
            with pytest.raises(ValueError, match="trigger_unsupported"):
                request_notice_enrichment(db, value, now=101)
        assert db.acquisitions() == [] and db.placements() == []


def test_purchase_context_revocation_leaves_no_intent(tmp_path):
    with closing(Storage(tmp_path / "revoked.sqlite")) as db:
        snapshot, candidate = prepare(db)
        persist_attested_source_references(db, snapshot)
        set_policy(db, "true")
        guard = iter((True, False))
        with pytest.raises(ValueError, match="policy_or_context"):
            request_notice_enrichment(
                db, ExistingPurchaseIntent(snapshot, (candidate,), lambda: next(guard)),
                now=100,
            )
        with db._lock, db._connect() as sql:
            assert sql.execute("SELECT count(*) FROM metadata WHERE key>=? AND key<?",
                               (journal._PREFIX, journal._END)).fetchone() == (0,)
