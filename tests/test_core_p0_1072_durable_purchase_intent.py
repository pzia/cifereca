"""Durable candidate fingerprints, no raw locator/title or executable grant."""
from dataclasses import replace
import pytest

from cifereca.core import notice_c_job_journal as journal
from cifereca.core import notice_c_purchase_intent as intent
from cifereca.core.canonical_sync_stage import persist_attested_source_references
from cifereca.plugin_api import PurchaseEnrichmentCandidate, from_legacy_rights_snapshot
from cifereca.storage import Storage
from tests.test_core_p0_3171_atomic_publication_v14 import new_source, register


def prepare(db, *, owner="A", provider="seller-a", credential="card-a", account="buyer-a"):
    register(db, owner, provider, credential, account=account)
    _, snapshot = new_source(owner, provider=provider, credential=credential, account=account)
    row, = from_legacy_rights_snapshot(snapshot).resources
    candidate = PurchaseEnrichmentCandidate(
        row.source, "https://catalogue.example.org/edition/123",
        "Titre achat synthétique", row.observed_at,
    )
    return snapshot, candidate


def set_policy(db, value):
    db.metadata_set(intent._POLICY, value)


def request(db, snapshot, *candidates, now=100, current=lambda: True):
    return intent.request_purchase_enrichment_intents(
        db, snapshot, tuple(candidates), now=now, context_current=current,
    )


def test_requires_a_commit_and_independent_policy(tmp_path):
    db = Storage(tmp_path / "work.sqlite")
    try:
        snapshot, candidate = prepare(db)
        with pytest.raises(ValueError, match="policy_or_context"):
            request(db, snapshot, candidate)
        set_policy(db, "true")
        with pytest.raises(ValueError, match="source_changed"):
            request(db, snapshot, candidate)
        persist_attested_source_references(db, snapshot)
        set_policy(db, "false")
        with pytest.raises(ValueError, match="policy_or_context"):
            request(db, snapshot, candidate)
        set_policy(db, "bad stored policy")
        with pytest.raises(ValueError, match="policy_or_context"):
            request(db, snapshot, candidate)
        set_policy(db, "true")
        ref, = request(db, snapshot, candidate)
        job = journal.read_job(db, ref)
        assert job.state == "pending" and job.attempts == 0
        assert journal.read_ready_page(db, now=100).refs == ()
        assert journal.read_ready_page(db, now=100, supported=(
            ("fetch_source_notice", journal._PURCHASE_INTENT_DIGEST),
        )).refs == (ref,)
        assert journal.claim_job(db, ref, now=100, lease_seconds=30) is None
        assert journal.read_job(db, ref) == job
        assert db.entitlements() == [] and db.acquisitions() == [] and db.placements() == []
    finally:
        db.close()


def test_restart_and_watermark_dedup_without_private_payload(tmp_path):
    path = tmp_path / "work.sqlite"
    db = Storage(path)
    try:
        snapshot, candidate = prepare(db)
        persist_attested_source_references(db, snapshot)
        set_policy(db, "true")
        ref, = request(db, snapshot, candidate)
        with db._lock, db._connect() as sql:
            raw, = sql.execute("SELECT value FROM metadata WHERE key=?",
                               (journal._PREFIX + ref.key,)).fetchone()
        for secret in (candidate.public_locator, candidate.title,
                       "buyer-a", "card-a", "positive-right-observation"):
            assert secret not in raw
    finally:
        db.close()
    db = Storage(path)
    try:
        latest = replace(snapshot.rights[0], evidence_ref="another-positive-receipt",
                         observed_at="2026-11-19T12:30:00Z")
        updated = replace(snapshot, rights=(latest,))
        persist_attested_source_references(db, updated)
        changed = replace(candidate, observed_at=latest.observed_at)
        assert request(db, updated, changed, now=101) == (ref,)
        with pytest.raises(ValueError, match="source_changed"):
            request(db, snapshot, candidate, now=101)
        changed = replace(changed, public_locator="https://catalogue.example.org/edition/456")
        renewed, = request(db, updated, changed, now=102)
        assert renewed.key == ref.key and renewed.generation == ref.generation + 1
        with pytest.raises(ValueError, match="generation_changed"):
            journal.read_job(db, ref)
        assert journal.read_job(db, renewed).state == "pending"
    finally:
        db.close()


def test_exact_profiles_independent_and_scope_mismatch_rejected(tmp_path):
    db = Storage(tmp_path / "scope.sqlite")
    try:
        a, ca = prepare(db)
        b, cb = prepare(db, owner="B", credential="card-b", account="buyer-b")
        persist_attested_source_references(db, a)
        persist_attested_source_references(db, b)
        set_policy(db, "true")
        with pytest.raises(ValueError, match="scope_changed"):
            request(db, a, cb)
        one, = request(db, a, ca)
        two, = request(db, b, cb)
        assert one.key != two.key
        with db._lock, db._connect() as sql:
            sql.execute("UPDATE provider_connections SET enabled=0 WHERE credential_id='card-b'")
        with pytest.raises(ValueError, match="policy_or_context"):
            request(db, b, cb)
        assert request(db, a, ca) == (one,)
    finally:
        db.close()


def test_final_guard_rolls_back_and_ambiguous_batch_is_refused(tmp_path):
    db = Storage(tmp_path / "guard.sqlite")
    try:
        snapshot, candidate = prepare(db)
        persist_attested_source_references(db, snapshot)
        set_policy(db, "true")
        with pytest.raises(ValueError, match="ambiguous_batch"):
            request(db, snapshot, candidate, candidate)
        guard = iter((True, False))
        with pytest.raises(ValueError, match="policy_or_context"):
            request(db, snapshot, candidate, current=lambda: next(guard))
        with db._lock, db._connect() as sql:
            assert sql.execute("SELECT count(*) FROM metadata WHERE key>=? AND key<?",
                               (journal._PREFIX, journal._END)).fetchone() == (0,)
            assert sql.execute("SELECT count(*) FROM source_references").fetchone() == (1,)
    finally:
        db.close()


def test_reserved_target_cannot_become_public_dependency(tmp_path):
    db = Storage(tmp_path / "dep.sqlite")
    try:
        snapshot, candidate = prepare(db)
        persist_attested_source_references(db, snapshot)
        set_policy(db, "true")
        ref, = request(db, snapshot, candidate)
        spec = journal.JobSpec(
            "recompute_aggregate", "notice-c:canonical-cover:v1:" + "a" * 64,
            "b" * 64, "c" * 64, (ref,),
        )
        with pytest.raises(ValueError, match="invalid_dependencies"):
            journal.request_job(db, spec, now=100, expected_generation=0)
        old = journal.read_job(db, ref).spec
        with pytest.raises(ValueError, match="invalid_purchase_intent"):
            journal.request_job(db, replace(old, target="notice-c:v1:" + "a"*64),
                                now=100, expected_generation=0)
    finally:
        db.close()
