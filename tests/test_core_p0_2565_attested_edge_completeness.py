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


def test_unique_historical_receipt_is_not_an_attested_purchase_origin():
    inventory, tables = _fixture(1)
    result = _reconcile(inventory, tables)
    assert result.files[0] == {
        "present_unlinked", "local_file_without_attested_right",
    }
    assert _attested(result) == []
    assert result.unverified_rights == [0]
    assert result.redacted()["edge_counts"]["explicit_placement_sha_size"] == 1
    assert ("explicit_right_ledger_placement_sha_size"
            not in result.redacted()["edge_counts"])


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


def test_deadline_expiring_before_canonical_promotion_cannot_attest(monkeypatch):
    from cifereca.core.canonical_publication import _stable
    inventory, tables = _fixture(1)
    origin = {
        "identity_id": "alice", "provider_id": "numilog",
        "credential_id": "credential", "account_id": "account",
        "branch_id": "personal_library", "edition_id": "edition-0000",
        "right_id": "right-0000", "reference_kind": "purchase",
        "state": "active", "evidence_kind": "provider_response",
        "evidence_ref": "observed-via-provider", "receipt_ref": "completed",
        "observed_at": "2026-11-16T09:00:00Z",
        "artifact_id": tables["artifacts"][0]["artifact_id"],
    }
    keys = (
        "identity_id", "provider_id", "credential_id", "account_id",
        "branch_id", "edition_id", "right_id", "reference_kind",
    )
    origin["source_reference_id"] = _stable("source", *(origin[k] for k in keys))
    origin["origin_link_id"] = _stable(
        "origin", "provider", origin["identity_id"], origin["artifact_id"],
        origin["source_reference_id"], origin["receipt_ref"],
    )
    tables["provider_origins"] = [origin]
    # Start, file entry, placement and artifact compare fit. The final
    # canonical-positive checkpoint (not a retired legacy right) expires.
    clock = iter((0.0, 0.0, 0.0, 0.0, 10.0))
    monkeypatch.setattr(engine.time, "monotonic", lambda: next(clock, 10.0))
    result = _reconcile(inventory, tables, seconds=10)
    assert result.reasons == {"comparison_budget"}
    assert result.unverified_rights == [0]
    assert _attested(result) == []
    assert "confirmed_link" not in result.files[0]


def test_deadline_inside_file_before_byte_verification_never_attests(monkeypatch):
    inventory, tables = _fixture(1)
    # Start, file and placement fit; artifact-byte verification is cut off
    # before any source or ownership evidence could be promoted.
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



def test_unknown_provider_competing_borrowers_are_ambiguous_without_any_grant():
    # A future plugin receives no implicit ownership from a plausible file,
    # receipt or entitlement. Its non-unique provider/edition placement is
    # ambiguous even though Core has no vendor-specific provenance mapping.
    inventory, tables = _fixture(1)
    for name in ("entitlements", "acquisition_ledger", "placements"):
        for row in tables[name]:
            row["provider_id"] = "synthetic-future-provider"
    second = dict(tables["entitlements"][0], entitlement_id="competing-right",
                  account_id="other-borrower", credential_id="other-credential",
                  state="inactive")
    tables["entitlements"].append(second)
    before = deepcopy(tables)
    report = _reconcile(inventory, tables)
    assert "ambiguous" in report.files[0]
    assert "confirmed_link" not in report.files[0]
    assert _attested(report) == []
    assert tables == before
    assert "competing-right" not in json.dumps(report.redacted())


def test_unrelated_legacy_scopes_keep_byte_evidence_without_false_ambiguity():
    inventory, tables = _fixture(1)
    observed = tables["entitlements"][0]
    different_identity = dict(observed, entitlement_id="foreign-right",
                              identity_id="bob", credential_id="bob-card")
    different_edition = dict(observed, entitlement_id="other-edition",
                             item_id="different-edition", account_id="other")
    tables["entitlements"].extend((different_identity, different_edition))
    report = _reconcile(inventory, tables)
    assert "ambiguous" not in report.files[0]
    assert report.files[0] == {
        "present_unlinked", "local_file_without_attested_right",
    }
    assert _attested(report) == []
    assert report.unverified_rights == [0, 1, 2]


def test_same_provider_edition_with_inactive_other_account_still_blocks():
    inventory, tables = _fixture(1)
    competitor = dict(tables["entitlements"][0], entitlement_id="inactive-other",
                      credential_id="other-credential", account_id="other-account",
                      state="inactive")
    tables["entitlements"].append(competitor)
    report = _reconcile(inventory, tables)
    assert "ambiguous" in report.files[0]
    assert "confirmed_link" not in report.files[0]
    assert _attested(report) == []
