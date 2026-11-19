"""A public detail proof is reusable; rights and receipts remain private."""
from contextlib import contextmanager, nullcontext
from dataclasses import replace
from types import SimpleNamespace

from cifereca.core.notice_c_purchase_composition import make_purchase_enrichment_sink
from cifereca.core.notice_c_purchase_execution import PurchaseIntentExecution
from cifereca.core.notice_c_purchase_association import read_purchase_notice_association
from cifereca.core.notice_c_purchase_public_detail_cache import read_public_detail_txn, _PREFIX
from cifereca.core.notice_c_work_bridge import AggregateWorkBridge
from cifereca.core.notice_c_public_search import search_public_notice_c
from cifereca.core.notice_cover_repository import read_notice_cover
from cifereca.core.notice_c_cover_index import persist_exact_public_search_cover
from cifereca.core.canonical_sync_stage import persist_attested_source_references
from cifereca.plugin_api import ProviderSearchResponse, PublicSearchNotice
from cifereca.storage import Storage
from tests.test_core_p0_1072_durable_purchase_intent import prepare, set_policy, request
from tests.test_core_p0_4982_fetch_execution import broker
from tests.test_core_p0_6366_real_search_notice_c_ingestion import public_row


def test_reuse_public_detail_across_restart_and_exact_accounts(tmp_path):
    path = tmp_path / "reuse.sqlite"
    calls = []
    session = SimpleNamespace(provider_id="seller-a-catalog", connected=True,
                              credential_id="__public__", credentials={}, _accounts=[])

    @contextmanager
    def context():
        yield True

    def read(url, title, *, check_authority, control):
        assert check_authority()
        calls.append("public")
        row = public_row(source_provider=session.provider_id, title=title,
                         friendly_url=url, thumbnail_url=None,
                         metadata={"public_url_evidence": session.provider_id + ".public_search_product_card"})
        return ProviderSearchResponse(results=[row], public_notices=(PublicSearchNotice(result=row),)), 99, nullcontext

    for owner, credential, account in (("A", "card-a", "buyer-a"), ("B", "card-b", "buyer-b")):
        db = Storage(path)
        tasks = broker()
        execution = PurchaseIntentExecution(db, clock=lambda: 100)
        try:
            snapshot, candidate = prepare(db, owner=owner, credential=credential, account=account)
            persist_attested_source_references(db, snapshot)
            set_policy(db, "true")
            sink = make_purchase_enrichment_sink(execution, private_provider_id="seller-a",
                public_session=session, read_response=read, hold_context=context, wake=lambda: None)
            assert sink(snapshot, (candidate,)).admitted == 1
            ref, = request(db, snapshot, candidate)
            bridge = AggregateWorkBridge(db, tasks, lambda *_: None, clock=lambda: 100,
                claim_purchase=execution.claim, prepare_purchase=execution.prepare)
            assert bridge.submit(ref).result(timeout=10) == "current"
            assert calls == ["public"]
            hit, = search_public_notice_c(db, "synthétique", now=100).hits
            persist_exact_public_search_cover(db, read_notice_cover(db, hit.document))
            receipt = read_purchase_notice_association(db, candidate.source, identity_id=owner)
            assert receipt is not None and receipt.observed_at == 99
            assert read_purchase_notice_association(db, candidate.source,
                identity_id="B" if owner == "A" else "A") is None
            with db._lock, db._connect() as sql:
                assert read_public_detail_txn(sql, session.provider_id, candidate, now=100) is not None
                assert read_public_detail_txn(sql, session.provider_id, replace(candidate, title="Homonyme différent"), now=100) is None
                assert read_public_detail_txn(sql, session.provider_id, candidate, now=100+7*86400) is None
                rows = sql.execute("SELECT key,value FROM metadata WHERE key>=? AND key<?", (_PREFIX, _PREFIX+"\uffff")).fetchall()
                assert len(rows) == 1
                for private in (account, credential, candidate.title, candidate.public_locator):
                    assert private not in repr(rows)
            assert db.entitlements() == [] and db.acquisitions() == [] and db.placements() == []
        finally:
            execution.close()
            tasks.close()
            db.close()
