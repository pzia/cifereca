"""P0 #2565/#2797: no 'attested' right edge from ambiguous/incomplete evidence.

Only the fully compared unique right/ledger/placement chain may be promoted.
An attested edge is consumed by private origin projection and redacted counters,
so it must not be staged before checking every competing source.
"""
from __future__ import annotations

from copy import deepcopy
import json

from cifereca.core import library_reconciliation as engine
from cifereca.core.library_evidence_scope import group_evidence
from cifereca.core.library_reconciliation import EvidenceSnapshot, reconcile
from tests.test_core_p0_2796_reconciliation_repeatability import _fixture


def _reconcile(inventory, tables, *, reasons=(), **kwargs):
    return reconcile(inventory, EvidenceSnapshot(tables, set(reasons)), **kwargs)


def _attested(report):
    return [edge for edge in report.edges if edge.certainty == "attested"]


def _duplicate_rights():
    inventory, tables = _fixture(1)
    duplicate = dict(tables["entitlements"][0], entitlement_id="second-owned-right")
    tables["entitlements"].append(duplicate)
    tables["acquisition_ledger"].append({
        **tables["acquisition_ledger"][0],
        "entitlement_id": duplicate["entitlement_id"],
        "acquisition_id": "second-receipt",
    })
    return inventory, tables


def test_unique_full_source_produces_exactly_one_attested_right_edge():
    inventory, tables = _fixture(1)
    result = _reconcile(inventory, tables)
    assert result.files[0] == {"confirmed_link"}
    assert len(_attested(result)) == 1
    assert _attested(result)[0].row_index == 0
    assert result.unverified_rights == []
    assert result.redacted()["edge_counts"]["explicit_right_ledger_placement_sha_size"] == 1


def test_two_attested_looking_rights_are_ambiguous_not_two_attestations():
    inventory, tables = _duplicate_rights()
    before = deepcopy(tables)
    result = _reconcile(inventory, tables)
    assert "confirmed_link" not in result.files[0]
    assert "ambiguous" in result.files[0]
    assert result.unverified_rights == [0, 1]
    assert _attested(result) == []
    public = result.redacted()
    assert "explicit_right_ledger_placement_sha_size" not in public["edge_counts"]
    groups = group_evidence(inventory, EvidenceSnapshot(tables), result, ("alice",))
    assert all(row["provider"] != "numilog" or
               row["counts"].get("confirmed_link", 0) == 0
               for row in groups.redacted()["groups"])
    assert tables == before
    assert "second-owned-right" not in json.dumps(public)


def test_duplicate_path_placements_cannot_publish_attested_edge():
    inventory, tables = _fixture(1)
    second = dict(tables["placements"][0], placement_id="second-placement")
    tables["placements"].append(second)
    result = _reconcile(inventory, tables)
    assert "ambiguous" in result.files[0]
    assert "confirmed_link" not in result.files[0]
    assert _attested(result) == []
    assert result.unverified_rights == [0]


def test_incomplete_snapshot_never_publishes_attested_edge():
    inventory, tables = _fixture(1)
    result = _reconcile(inventory, tables, reasons={"snapshot_row_budget"})
    assert result.reasons == {"snapshot_row_budget"}
    assert _attested(result) == []
    assert "confirmed_link" not in result.files[0]
    assert result.unverified_rights == [0]


def test_deadline_expiring_inside_single_file_cannot_attest(monkeypatch):
    inventory, tables = _fixture(1)
    # Start, file entry, placement, right are inside the window. The
    # final proof-promotion check happens exactly on the deadline.
    clock = iter((0.0, 0.0, 0.0, 0.0, 10.0))
    monkeypatch.setattr(engine.time, "monotonic", lambda: next(clock, 10.0))
    result = _reconcile(inventory, tables, seconds=10)
    assert result.reasons == {"comparison_budget"}
    assert result.unverified_rights == [0]
    assert _attested(result) == []
    assert "confirmed_link" not in result.files[0]


def test_deadline_inside_one_file_before_right_verification_never_attests(monkeypatch):
    inventory, tables = _fixture(1)
    # The source-agnostic account-ambiguity gate now rejects *both* Kobo
    # and Numilog editions with multiple accounts before inner comparison.
    # Keep this independent deadline test on an otherwise unique right.
    # Start, file entry and placement fit; right verification expires.
    ticks = iter((0, 0, 0, 10))
    monkeypatch.setattr(engine.time, "monotonic", lambda: next(ticks, 10))
    result = _reconcile(inventory, tables, seconds=10)
    assert result.reasons == {"comparison_budget"}
    assert "confirmed_link" not in result.files[0]
    assert _attested(result) == []
    assert result.unverified_rights == [0]


def test_cancellation_inside_selected_file_revokes_tentative_link():
    inventory, tables = _fixture(1)
    calls = []

    def cancelled():
        calls.append(True)
        return len(calls) >= 4

    result = _reconcile(inventory, tables, cancelled=cancelled)
    assert len(calls) >= 4
    assert result.reasons == {"cancelled"}
    assert "confirmed_link" not in result.files[0]
    assert _attested(result) == []
    assert result.unverified_rights == [0]

