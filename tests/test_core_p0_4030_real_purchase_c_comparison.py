"""#4030: exact private purchase comparison against the real persisted C reader."""
from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from cifereca.core import notice_c_job_journal as journal
from cifereca.core.canonical_sync_stage import persist_attested_source_references
from cifereca.core.local_origin_private_catalogue import compare_known_private_catalogue
from cifereca.core.notice_c_cover_index import persist_exact_public_search_cover
from cifereca.core.notice_c_purchase_association import record_purchase_association_txn
from cifereca.core.notice_c_purchase_descriptive_read import read_purchase_descriptive_notice
from cifereca.core.notice_c_purchase_execution import PurchaseIntentExecution
from cifereca.core.notice_c_purchase_publication import publish_purchase_public_txn
from cifereca.models import Account
from tests.test_core_p0_4030_public_catalogue import rig, rows, store
from tests.test_core_p0_1072_durable_purchase_intent import prepare, set_policy, request
from tests.test_core_p0_1072_nondestructive_purchase_publication import observation


@contextmanager
def _allowed():
    yield True


def _seed_c(storage, *, indexed):
    snapshot, candidate = prepare(storage)
    candidate = replace(candidate, title="Roman commun")
    persist_attested_source_references(storage, snapshot)
    set_policy(storage, "true")
    ref, = request(storage, snapshot, candidate)
    execution = PurchaseIntentExecution(storage, clock=lambda: 100)
    try:
        assert execution.attest(ref, snapshot, candidate, hold_context=_allowed,
                                execute=lambda *_: None, ttl_seconds=30)
        lease = execution.claim(ref, now=100, lease_seconds=30)
        assert lease is not None
        spec = journal.read_job(storage, ref).spec
        incoming = observation(candidate, at=98)
        with storage._lock, storage._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            published = publish_purchase_public_txn(db, spec, snapshot, candidate, incoming, now=100)
            record_purchase_association_txn(
                db, lease, snapshot, candidate, published, observed_at=99, now=100,
            )
            journal._complete_job_txn(db, lease, now=100)
        if indexed:
            persist_exact_public_search_cover(storage, published.source)
    finally:
        execution.close()
    set_policy(storage, "false")
    return snapshot, candidate


@pytest.mark.parametrize("case", ["qualified", "unindexed", "changed_a", "disabled"])
def test_real_c_private_purchase_lookup_preserves_every_persisted_fact(rig, case):
    service, _, review, calls, path = rig
    storage = service.storage
    snapshot, candidate = _seed_c(storage, indexed=case != "unindexed")
    scope = candidate.source.scope
    account = Account(identity_id=scope.identity_id, provider_id=scope.provider_id,
                      credential_id=scope.credential_id, unique_id=scope.account_id)
    session = NS(provider_id=scope.provider_id, credential_id=scope.credential_id,
                 connected=True, accounts=[account])
    service.accounts = [account]
    service._sessions["private-purchase"] = session
    service.sync_source_enabled = lambda a: a is account
    service._provider_enabled_for_identity = lambda p, i: p == scope.provider_id and i == scope.identity_id
    if case == "changed_a":
        persist_attested_source_references(storage, replace(snapshot, rights=(
            replace(snapshot.rights[0], evidence_ref="new-provider-witness",
                    observed_at="2026-11-20T12:00:00Z"),)))
    elif case == "disabled":
        with storage._lock, storage._connect() as db:
            db.execute("UPDATE provider_connections SET enabled=0 WHERE credential_id=?",
                       (scope.credential_id,))
    def persisted():
        with storage._lock, storage._connect() as db:
            return tuple(db.execute("SELECT key,value FROM metadata ORDER BY key").fetchall())
    before, bytes_before = (rows(storage), persisted()), path.read_bytes()
    notice = read_purchase_descriptive_notice(storage, candidate.source, identity_id="A")
    result = compare_known_private_catalogue(service, review, account=account, kind="purchase",
                                             confirmed=True)
    if case == "qualified":
        assert notice is not None
        match, = result["candidates"]
        assert result["status"] == "partial"
        assert match.source == candidate.source
        assert match.title == "Roman commun" and match.author == "Même auteur"
        assert match.comparison == "title_concordant_author_divergent"
        assert match.title_observed_at == 98  # source date, not association time 99
        assert match.evidence == "cached_public_purchase_description"
    else:
        assert notice is None and not result["candidates"]
    assert not any(result[k] for k in ("coverage_complete", "fresh_network_verified",
        "provider_access_performed", "association_authorized", "may_write_origin_or_right"))
    assert not calls and path.read_bytes() == bytes_before
    assert (rows(storage), persisted()) == before
