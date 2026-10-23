"""P0 #2565/#2734: interleaved profiles keep independent bounded drift baselines.

These digests are local, ephemeral diagnostics. They never attest a right or
leave the Core broker as raw book/account/path observations.
"""
from __future__ import annotations

from dataclasses import replace
import json

from cifereca.core import library_audit_stability as stability
from cifereca.core.library_audit_stability import (
    begin_audit_stability, finish_audit_stability,
)
from tests.test_core_p0_2796_private_audit_stability import _audit
from tests.test_core_p0_2796_reconciliation_repeatability import _fixture


def _run(identity="alice", *, changed=False):
    inventory, tables = _fixture(3)
    # Full proof projection needs the (possibly empty) Kobo backup table too.
    tables["backup_items"] = []
    if identity != "alice":
        inventory.files = [replace(file, identity_id=identity)
                           for file in inventory.files]
        for name in ("entitlements", "acquisition_ledger", "placements"):
            for row in tables[name]:
                row["identity_id"] = identity
    if changed:
        tables["placements"][0]["path"] += ".obsolete"
    return _audit(inventory, tables)


def _commit(owner, identity, scope, audit):
    generation, previous = begin_audit_stability(owner, identity, scope)
    result = finish_audit_stability(
        owner, identity, scope, generation, previous, audit, lambda: True,
    )
    return result


def test_interleaved_profiles_preserve_explanation_of_exact_link_loss():
    owner, scope = object(), ("same-service", "stable-roots")
    alice, bob = _run("alice"), _run("bob")
    assert _commit(owner, "alice", scope, alice) is None
    assert _commit(owner, "bob", scope, bob) is None

    report_alice = _commit(owner, "alice", scope, _run("alice", changed=True))
    assert report_alice is not None
    assert report_alice["same_observed_cohort"] is True
    assert report_alice["same_file_evidence"] is True
    assert report_alice["changed_sqlite_tables"] == ["placements"]
    assert report_alice["lost_confirmed"] == 1
    assert report_alice["interpretation"] == "sqlite_proofs_changed"

    report_bob = _commit(owner, "bob", scope, _run("bob"))
    assert report_bob is not None
    assert report_bob["lost_confirmed"] == 0
    assert report_bob["interpretation"] == "stable_inputs"
    for report in (report_alice, report_bob):
        serialized = json.dumps(report)
        assert "PRIVATE_LIBRARY" not in serialized
        assert "credential" not in serialized
        assert "entitlement-" not in serialized


def test_other_identity_started_during_audit_cannot_revoke_its_result():
    owner, scope = object(), ("interleaved",)
    ga, prior_a = begin_audit_stability(owner, "alice", scope)
    gb, prior_b = begin_audit_stability(owner, "bob", scope)
    assert prior_a is prior_b is None
    assert finish_audit_stability(
        owner, "alice", scope, ga, None, _run("alice"), lambda: True,
    ) is None
    assert finish_audit_stability(
        owner, "bob", scope, gb, None, _run("bob"), lambda: True,
    ) is None
    assert _commit(owner, "alice", scope, _run("alice"))["basis"] == "complete"
    assert _commit(owner, "bob", scope, _run("bob"))["basis"] == "complete"


def test_same_identity_new_generation_or_scope_revokes_only_that_identity():
    owner, scope = object(), ("initial",)
    g0, _ = begin_audit_stability(owner, "alice", scope)
    gb, _ = begin_audit_stability(owner, "bob", scope)
    g1, _ = begin_audit_stability(owner, "alice", ("changed-root",))
    assert finish_audit_stability(
        owner, "alice", scope, g0, None, _run(), lambda: True,
    ) is None
    assert finish_audit_stability(
        owner, "alice", ("changed-root",), g1, None, _run(), lambda: True,
    ) is None
    assert finish_audit_stability(
        owner, "bob", scope, gb, None, _run("bob"), lambda: True,
    ) is None
    assert _commit(owner, "alice", scope, _run()) is None  # scope changed
    assert _commit(owner, "bob", scope, _run("bob"))["interpretation"] == "stable_inputs"


def test_different_service_with_same_identity_keeps_independent_baseline():
    owners = [object(), object()]
    for owner in owners:
        assert _commit(owner, "alice", ("owner",), _run()) is None
    for owner in reversed(owners):
        assert _commit(owner, "alice", ("owner",), _run())["interpretation"] == "stable_inputs"


def test_private_cache_bounded_across_services_and_incomplete_projection_expires(monkeypatch):
    # A small cap exercises eviction without accumulating 100,000-row proofs.
    monkeypatch.setattr(stability, "_MAX_SLOTS", 2)
    owners = [object() for _ in range(3)]
    for index, owner in enumerate(owners):
        assert _commit(owner, "alice", ("bounded", index), _run()) is None
    assert len(stability._SLOTS) <= 2
    assert _commit(owners[0], "alice", ("bounded", 0), _run()) is None
    assert _commit(owners[2], "alice", ("bounded", 2), _run())["interpretation"] == "stable_inputs"

    # A consumed baseline does not survive an explicitly changed scope.
    owner = object()
    assert _commit(owner, "alice", ("old",), _run()) is None
    assert _commit(owner, "alice", ("new",), _run()) is None


def test_private_cache_aggregate_digest_budget_never_evicts_foreign_active_run(monkeypatch):
    monkeypatch.setattr(stability, "_MAX_RETAINED_ENTRIES", 20)
    owner, scope = object(), ("bounded",)
    ga, _ = begin_audit_stability(owner, "alice", scope)
    gb, _ = begin_audit_stability(owner, "bob", scope)
    assert finish_audit_stability(
        owner, "alice", scope, ga, None, _run("alice"), lambda: True,
    ) is None
    assert finish_audit_stability(
        owner, "bob", scope, gb, None, _run("bob"), lambda: True,
    ) is None
    assert sum(stability._retained_entries(slot.value)
               for slot in stability._SLOTS) <= stability._MAX_RETAINED_ENTRIES
    assert len(stability._ACTIVE) <= stability._MAX_ACTIVE
    # Check the most recent surviving profile *before* adding A again:
    # the aggregate quota intentionally evicts the oldest baseline.
    assert _commit(owner, "bob", scope, _run("bob"))["interpretation"] == "stable_inputs"
    assert _commit(owner, "alice", scope, _run("alice")) is None
