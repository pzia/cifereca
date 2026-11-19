"""Full Core A handoff → lease → anonymous producer → C transaction.

Synthetic public transports, real SQLite and existing broker; no external GET.
"""
from contextlib import contextmanager, nullcontext
from types import SimpleNamespace

import pytest

from cifereca.core import notice_c_job_journal as journal
from cifereca.core.notice_c_purchase_composition import make_purchase_enrichment_sink
from cifereca.core.notice_c_purchase_execution import PurchaseIntentExecution
from cifereca.core.notice_c_work_bridge import AggregateWorkBridge
from cifereca.core.notice_c_public_search import search_public_notice_c
from cifereca.core.canonical_sync_stage import persist_attested_source_references
from cifereca.plugin_api import ProviderSearchResponse, PublicSearchNotice
from cifereca.storage import Storage
from tests.test_core_p0_1072_durable_purchase_intent import prepare, set_policy, request
from tests.test_core_p0_4982_fetch_execution import broker
from tests.test_core_p0_6366_real_search_notice_c_ingestion import public_row


@pytest.mark.parametrize("provider", ("seller-a", "seller-b"))
@pytest.mark.parametrize("revoked", (False, True))
def test_composed_core_handoff_and_real_publication(tmp_path, provider, revoked):
    db = Storage(tmp_path / "compose.sqlite")
    tasks = broker()
    execution = PurchaseIntentExecution(db, clock=lambda: 100)
    calls = []
    guard = [True]
    public_provider = provider + "-catalog"
    public_session = SimpleNamespace(provider_id=public_provider, connected=True,
                                     credential_id="__public__", credentials={}, _accounts=[])

    @contextmanager
    def context():
        yield guard[0]

    try:
        snapshot, candidate = prepare(db, provider=provider)
        persist_attested_source_references(db, snapshot)
        set_policy(db, "true")

        def read(url, title, *, check_authority, control):
            assert url == candidate.public_locator
            assert title == candidate.title and check_authority()
            calls.append("public")
            row = public_row(
                source_provider=public_provider, title="Titre achat synthétique",
                friendly_url=url, thumbnail_url=None,
                metadata={"public_url_evidence": public_provider + ".public_search_product_card"},
            )
            if revoked:
                guard[0] = False
            return ProviderSearchResponse(
                results=[row], public_notices=(PublicSearchNotice(result=row),),
            ), 99, nullcontext

        sink = make_purchase_enrichment_sink(
            execution, private_provider_id=provider, public_session=public_session,
            read_response=read, hold_context=context, wake=lambda: calls.append("wake"),
        )
        result = sink(snapshot, (candidate,))
        assert (result.requested, result.admitted, result.deferred) == (1, 1, 0)
        assert calls == ["wake"]
        ref, = request(db, snapshot, candidate)
        bridge = AggregateWorkBridge(
            db, tasks, lambda *_: pytest.fail("not aggregate"), clock=lambda: 100,
            claim_purchase=execution.claim, prepare_purchase=execution.prepare,
        )
        outcome = bridge.submit(ref).result(timeout=10)
        assert calls == ["wake", "public"]
        hits = search_public_notice_c(db, "synthétique", now=100).hits
        if revoked:
            assert outcome == "error" and not hits
        else:
            assert outcome == "current" and len(hits) == 1
            assert hits[0].last_seen_at == 99 and hits[0].provider_id == public_provider
            again = sink(snapshot, (candidate,))
            assert (again.requested, again.admitted, again.deferred) == (1, 0, 0)
            assert calls == ["wake", "public"]
        assert journal.read_job(db, ref).state == outcome
        assert db.entitlements() == [] and db.acquisitions() == [] and db.placements() == []
    finally:
        execution.close()
        tasks.close()
        db.close()
