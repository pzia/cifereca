"""A public detail proof is reusable; rights and receipts remain private."""
from contextlib import contextmanager, nullcontext
import json
import pytest
from dataclasses import replace
from types import SimpleNamespace

from cifereca.core.notice_c_purchase_composition import make_purchase_enrichment_sink
from cifereca.core.notice_c_purchase_execution import PurchaseIntentExecution
from cifereca.core.notice_c_purchase_association import read_purchase_notice_association
from cifereca.core import notice_c_purchase_public_detail_cache as cache
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


@pytest.mark.parametrize('pressure', (False, True))
def test_reuse_public_detail_across_restart_and_exact_accounts(tmp_path, monkeypatch, pressure):
    if pressure:
        monkeypatch.setattr(cache, '_MAX', 1)
    now = 7 * 86400 + 100 if pressure else 100
    expired_key = _PREFIX + 'f' * 64
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
        return ProviderSearchResponse(results=[row], public_notices=(PublicSearchNotice(result=row),)), now - 1, nullcontext

    source_document = None
    for owner, credential, account in (("A", "card-a", "buyer-a"), ("B", "card-b", "buyer-b")):
        db = Storage(path)
        if pressure and owner == 'A':
            with db._lock, db._connect() as sql:
                sql.execute('INSERT INTO metadata(key,value) VALUES(?,?)',
                            (expired_key, json.dumps({'observed_at': 1})))
        tasks = broker()
        execution = PurchaseIntentExecution(db, clock=lambda: now)
        try:
            snapshot, candidate = prepare(db, owner=owner, credential=credential, account=account)
            persist_attested_source_references(db, snapshot)
            set_policy(db, "true")
            sink = make_purchase_enrichment_sink(execution, private_provider_id="seller-a",
                public_session=session, read_response=read, hold_context=context, wake=lambda: None)
            assert sink(snapshot, (candidate,)).admitted == 1
            ref, = request(db, snapshot, candidate, now=now)
            bridge = AggregateWorkBridge(db, tasks, lambda *_: None, clock=lambda: now,
                claim_purchase=execution.claim, prepare_purchase=execution.prepare)
            assert bridge.submit(ref).result(timeout=10) == "current"
            assert calls == ["public"]
            hit, = search_public_notice_c(db, "synthétique", now=now).hits
            if source_document is None:
                # First search before aggregate publication exposes source C;
                # subsequent search exposes the canonical DocumentRef.
                source_document = hit.document
                retained = read_notice_cover(db, source_document)
                assert retained is not None
                persist_exact_public_search_cover(db, retained)
            else:
                assert read_notice_cover(db, source_document) is not None
            receipt = read_purchase_notice_association(db, candidate.source, identity_id=owner)
            assert receipt is not None and receipt.observed_at == now - 1
            assert read_purchase_notice_association(db, candidate.source,
                identity_id="B" if owner == "A" else "A") is None
            with db._lock, db._connect() as sql:
                assert read_public_detail_txn(sql, session.provider_id, candidate, now=now) is not None
                assert read_public_detail_txn(sql, session.provider_id, replace(candidate, title="Homonyme différent"), now=now) is None
                assert read_public_detail_txn(sql, session.provider_id, candidate, now=now+7*86400) is None
                rows = sql.execute("SELECT key,value FROM metadata WHERE key>=? AND key<?", (_PREFIX, _PREFIX+"\uffff")).fetchall()
                assert len(rows) == 1 and rows[0][0] != expired_key
                for private in (account, credential, candidate.title, candidate.public_locator):
                    assert private not in repr(rows)
            assert db.entitlements() == [] and db.acquisitions() == [] and db.placements() == []
        finally:
            execution.close()
            tasks.close()
            db.close()


def test_full_public_receipt_cache_does_not_evict_current_or_unknown(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "_MAX", 4)
    db = Storage(tmp_path / "pressure.sqlite")
    now = 7 * 86400 + 100
    expired = _PREFIX + "0" * 64
    current = _PREFIX + "1" * 64
    future = _PREFIX + "2" * 64
    unknown = _PREFIX + "3" * 64
    other = "unrelated.metadata"
    try:
        with db._lock, db._connect() as sql:
            rows = {
                expired: json.dumps({"observed_at": 100}),
                current: json.dumps({"observed_at": 101}),
                future: json.dumps({"observed_at": now + 1}),
                unknown: "{}",
                other: json.dumps({"observed_at": 1}),
            }
            sql.executemany("INSERT INTO metadata(key,value) VALUES(?,?)", rows.items())
            assert cache._reclaim_expired_public_details_txn(sql, now=now) == 1
            assert cache._reclaim_expired_public_details_txn(sql, now=now) == 0
            for key in (current, future, unknown, other):
                assert sql.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()[0] == rows[key]
            assert sql.execute("SELECT value FROM metadata WHERE key=?", (expired,)).fetchone() is None
    finally:
        db.close()
