"""One inventory, several durable chunks, one bounded live registry."""
from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace

import pytest

from cifereca.core import notice_c_job_journal as journal
from cifereca.core.notice_c_purchase_composition import make_purchase_enrichment_sink
from cifereca.core.notice_c_purchase_execution import PurchaseIntentExecution
from cifereca.core.canonical_sync_stage import persist_attested_source_references
from cifereca.storage import Storage
from tests.test_core_p0_1072_durable_purchase_intent import prepare, set_policy


def inventory(db):
    seed, candidate = prepare(db)
    rows, candidates = [], []
    for n in range(65):
        edition, right = "edition-" + str(n), "purchase-" + str(n)
        row = replace(seed.rights[0], edition_id=edition, right_id=right)
        rows.append(row)
        candidates.append(replace(
            candidate, source=replace(candidate.source, edition_id=edition, resource_id=right),
            public_locator="https://catalogue.example.org/edition/" + str(n),
        ))
    snapshot = replace(seed, rights=tuple(rows))
    persist_attested_source_references(db, snapshot)
    set_policy(db, "true")
    return snapshot, tuple(candidates)


def make_sink(execution, context, calls):
    def forbidden(*args, **kwargs):
        pytest.fail("no HTTP in handoff")
    return make_purchase_enrichment_sink(
        execution, private_provider_id="seller-a",
        public_session=SimpleNamespace(provider_id="seller-a-catalog", connected=True,
                                       credential_id="__public__", credentials={}, _accounts=[]),
        read_response=forbidden, hold_context=context,
        wake=lambda: calls.append("wake"),
    )


def jobs(db):
    with db._lock, db._connect() as sql:
        keys = sql.execute("SELECT key FROM metadata WHERE key>=? AND key<?",
                           (journal._PREFIX, journal._END)).fetchall()
        return tuple(journal._load(sql, key[len(journal._PREFIX):]) for key, in keys)


@pytest.mark.parametrize("interrupted", (False, True))
def test_all_intents_or_explicit_unrequested_suffix(tmp_path, interrupted):
    db = Storage(tmp_path / "batch.sqlite")
    execution = PurchaseIntentExecution(db, clock=lambda: 100)
    calls = []

    @contextmanager
    def context():
        yield not interrupted or len(execution._live) < 32

    try:
        snapshot, candidates = inventory(db)
        result = make_sink(execution, context, calls)(snapshot, candidates)
        assert result.requested == (32 if interrupted else 65)
        assert result.unrequested == (33 if interrupted else 0)
        assert result.admitted == 32
        assert result.deferred == (0 if interrupted else 33)
        initial = jobs(db)
        assert len(initial) == result.requested
        assert all(j.state == "pending" and j.attempts == 0 for j in initial)
        assert calls == ["wake"]
        assert all(len(item.snapshot.rights) <= 32 and
                   not item.snapshot.claims_complete_branch for item in execution._live.values())
        # A later authorized inventory can reattest any pending suffix.
        execution.close()
        replacement = PurchaseIntentExecution(db, clock=lambda: 101)

        @contextmanager
        def available():
            yield True

        try:
            result = make_sink(replacement, available, calls)(snapshot, tuple(reversed(candidates)))
            assert (result.requested, result.admitted, result.deferred, result.unrequested) == (65, 32, 33, 0)
            resumed = jobs(db)
            assert len(resumed) == 65
            assert set(j.ref for j in initial) <= set(j.ref for j in resumed)
            assert all(j.attempts == 0 for j in resumed)
            assert calls == ["wake", "wake"]
        finally:
            replacement.close()
        assert db.entitlements() == [] and db.acquisitions() == [] and db.placements() == []
    finally:
        execution.close()
        db.close()


def test_duplicate_across_chunks_refuses_before_first_intent(tmp_path):
    db = Storage(tmp_path / "duplicate.sqlite")
    execution = PurchaseIntentExecution(db, clock=lambda: 100)

    @contextmanager
    def available():
        yield True

    try:
        snapshot, candidates = inventory(db)
        with pytest.raises(ValueError, match="invalid_handoff"):
            make_sink(execution, available, [])(snapshot, candidates + (candidates[0],))
        assert jobs(db) == () and execution.ready_refs() == ()
    finally:
        execution.close()
        db.close()
