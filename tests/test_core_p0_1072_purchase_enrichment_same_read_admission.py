"""Independent same-read C candidate handoff only after real committed A."""
from dataclasses import replace
import pytest

from cifereca.core import canonical_sync_stage as stage
from cifereca.plugin_api import (
    PurchaseEnrichmentCandidate, PurchaseEnrichmentObservation, PurchaseEnrichmentOmission,
    from_legacy_rights_snapshot,
)
from tests.test_core_p0_3171_canonical_sync_stage import rig, refs


def install(service, *, mode="valid"):
    calls = []
    def alternative(account):
        assert account is service.account
        calls.append("same-read")
        inventory = from_legacy_rights_snapshot(service.snapshot)
        resource, = inventory.resources
        candidate = PurchaseEnrichmentCandidate(
            resource.source, "https://catalog.example.org/product/123",
            "Titre synthétique", resource.observed_at,
        )
        result = PurchaseEnrichmentObservation(inventory, (candidate,))
        if mode == "malformed":
            object.__setattr__(candidate, "public_locator", "https://private.example/?token=secret")
        elif mode == "rebound":
            service._source_context_epoch += 1
        elif mode == "cursor":
            object.__setattr__(result, "inventory", replace(inventory, resume_cursor="next"))
        return result
    service.session.purchase_enrichment_observation = alternative
    return calls


def test_normal_rights_read_does_not_activate_optional_port(rig):
    service, db, root = rig
    calls = install(service)
    summary = stage.observe_canonical_rights_in_sync(service, service.account)
    assert calls == [] and service.read_calls == 1
    assert summary["source_references_changed"] == 1
    assert len(refs(db)[0]) == 1 and not root.exists()


@pytest.mark.parametrize("mode", ["valid", "malformed", "cursor", "sink_error"])
def test_only_valid_candidates_reach_sink_after_real_commit_without_second_get(rig, mode):
    service, db, root = rig
    calls = install(service, mode=mode)
    handoffs = []
    def sink(snapshot, candidates):
        # Not called within the A lock or a pending SQLite transaction.
        assert len(refs(db)[0]) == 1
        assert not db._connect().in_transaction
        handoffs.append((snapshot, candidates))
        if mode == "sink_error":
            raise RuntimeError("private exception must not affect A")
    summary = stage.observe_canonical_rights_in_sync(
        service, service.account, purchase_enrichment_sink=sink,
    )
    assert calls == ["same-read"] and service.read_calls == 0
    assert summary["source_references_changed"] == 1
    assert summary["status"] == service.snapshot.status
    assert len(handoffs) == int(mode in ("valid", "sink_error"))
    if handoffs:
        snapshot, (candidate,) = handoffs[0]
        assert candidate.source.scope == snapshot.scope
        assert candidate.public_locator == "https://catalog.example.org/product/123"
        assert candidate.authorizes_fetch is False
    assert "catalog.example" not in repr(summary)
    assert len(refs(db)[0]) == 1
    assert db.entitlements() == [] and db.acquisitions() == [] and db.placements() == []
    assert not root.exists()


def test_rebound_before_a_commit_never_hands_off_candidates(rig):
    service, db, root = rig
    calls = install(service, mode="rebound")
    handoffs = []
    with pytest.raises(ValueError, match="canonical_rights_source_rebound"):
        stage.observe_canonical_rights_in_sync(
            service, service.account, purchase_enrichment_sink=lambda *args: handoffs.append(args),
        )
    assert calls == ["same-read"] and service.read_calls == 0
    assert refs(db) == ([], 0) and handoffs == []


def test_a_transaction_failure_never_hands_off_candidate(rig, monkeypatch):
    service, db, root = rig
    calls = install(service)
    handoffs = []
    def fail(*args, **kwargs):
        raise ValueError("simulated_a_transaction_failure")
    monkeypatch.setattr(stage, "persist_attested_source_references", fail)
    with pytest.raises(ValueError, match="simulated_a_transaction_failure"):
        stage.observe_canonical_rights_in_sync(
            service, service.account, purchase_enrichment_sink=lambda *args: handoffs.append(args),
        )
    assert calls == ["same-read"] and service.read_calls == 0
    assert refs(db) == ([], 0) and handoffs == []


def test_explicit_omission_after_a_commit_is_scoped_and_reversible(rig):
    from cifereca.core.notice_c_purchase_library_projection import _status_txn
    from cifereca.core.notice_c_purchase_omissions import _PREFIX
    from tests.test_core_p0_1072_durable_purchase_intent import set_policy, request

    service, db, _ = rig
    calls = []
    set_policy(db, "true")
    snapshot = service.snapshot
    inventory = from_legacy_rights_snapshot(snapshot)
    resource, = inventory.resources
    candidate = PurchaseEnrichmentCandidate(resource.source, "https://catalog.example.org/product/123",
                                             "Title", resource.observed_at)
    omitted = PurchaseEnrichmentOmission(resource.source, resource.observed_at,
                                         "direct_locator_unattested")
    report = [PurchaseEnrichmentObservation(inventory, omissions=(omitted,))]

    def same_read(account):
        assert account is service.account
        calls.append("read")
        return report[0]

    service.session.purchase_enrichment_observation = same_read
    received = []
    def sink(*args):
        received.append(args)

    summary = stage.observe_canonical_rights_in_sync(service, service.account,
                                                     purchase_enrichment_sink=sink)
    assert summary["source_references_changed"] == 1 and received == []
    with db._lock, db._connect() as sql:
        assert _status_txn(sql, resource.source, now=100) == ("direct_locator_unattested", 0)
        rows = sql.execute("SELECT key,value FROM metadata WHERE key>=? AND key<?",
                           (_PREFIX, _PREFIX+"\uffff")).fetchall()
        assert len(rows) == 1
        assert all(private not in repr(rows) for private in ("card-a", "buyer-a", "Title", candidate.public_locator))
    with pytest.raises(ValueError, match="notice_purchase_candidate_omitted"):
        request(db, snapshot, candidate)
    report[0] = PurchaseEnrichmentObservation(inventory, (candidate,))
    stage.observe_canonical_rights_in_sync(service, service.account, purchase_enrichment_sink=sink)
    assert calls == ["read", "read"] and len(received) == 1
    with db._lock, db._connect() as sql:
        assert _status_txn(sql, resource.source, now=100) is None
        assert sql.execute("SELECT count(*) FROM metadata WHERE key>=? AND key<?",
                           (_PREFIX, _PREFIX+"\uffff")).fetchone() == (0,)
    assert len(request(db, snapshot, candidate)) == 1
    assert db.entitlements() == [] and db.acquisitions() == [] and db.placements() == []
