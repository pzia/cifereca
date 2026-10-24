"""P0 #2797/#2565: placement bytes do not identify Kobo or Numilog buyer.

The placement table stores provider + identity + edition, but NOT buyer
credential/account. A competing same-identity right (including a stale one
without an acquisition) makes the account-specific origin unprovable.
"""
from __future__ import annotations

from copy import deepcopy
import json

import pytest

from cifereca.core.library_evidence_scope import group_evidence
from cifereca.core.library_reconciliation import EvidenceSnapshot, reconcile
from tests.test_core_p0_2796_reconciliation_repeatability import _fixture


@pytest.mark.parametrize("provider,source", [
    ("kobo", "kobo.personal_manifest"),
    ("numilog", "numilog.authenticated_library"),
])
@pytest.mark.parametrize("competitor_state", ["active", "realized"])
def test_same_edition_second_buyer_without_receipt_blocks_attestation(
    provider, source, competitor_state,
):
    inventory, tables = _fixture(1)
    first = tables["entitlements"][0]
    first["provider_id"] = provider
    first["attested_source"] = source
    tables["acquisition_ledger"][0]["provider_id"] = provider
    tables["placements"][0]["provider_id"] = provider
    # A legacy/synced right for a DIFFERENT account of the same local
    # identity may not have a Core acquisition receipt. The placement
    # does not identify which right is the origin.
    other = {
        **first,
        "entitlement_id": "other-private-right",
        "credential_id": "other-credential",
        "account_id": "other-buyer",
        "state": competitor_state,
    }
    tables["entitlements"].append(other)
    unchanged = deepcopy(tables)
    report = reconcile(inventory, EvidenceSnapshot(tables))
    assert "confirmed_link" not in report.files[0]
    assert "ambiguous" in report.files[0]
    assert "local_file_without_attested_right" in report.files[0]
    assert [e for e in report.edges if e.certainty == "attested"] == []
    # Raw matching bytes are still observable as BYTE evidence, not rights.
    assert any(e.certainty == "bytes" for e in report.edges)
    assert 0 in report.unverified_rights
    if competitor_state == "active":
        assert report.unverified_rights == [0, 1]
    else:
        assert report.unverified_rights == [0]
    grouped = group_evidence(inventory, EvidenceSnapshot(tables), report, ("alice",))
    assert any(row["provider"] == "unattributed"
               and row["counts"].get("ambiguous") == 1
               for row in grouped.redacted()["groups"])
    public = json.dumps(report.redacted()) + json.dumps(grouped.redacted())
    assert "other-buyer" not in public
    assert "other-private-right" not in public
    assert inventory.files[0].path not in public
    assert inventory.files[0].sha256 not in public
    assert tables == unchanged


@pytest.mark.parametrize("provider,source", [
    ("kobo", "kobo.personal_manifest"),
    ("numilog", "numilog.authenticated_library"),
])
@pytest.mark.parametrize("foreign_dimension", ["identity", "edition", "provider"])
def test_unrelated_scope_does_not_block_unique_owned_chain(
    provider, source, foreign_dimension,
):
    inventory, tables = _fixture(1)
    right = tables["entitlements"][0]
    right["provider_id"] = provider
    right["attested_source"] = source
    tables["acquisition_ledger"][0]["provider_id"] = provider
    tables["placements"][0]["provider_id"] = provider
    other = {**right, "entitlement_id": "other-right"}
    if foreign_dimension == "identity":
        other["identity_id"] = "other-identity"
    elif foreign_dimension == "edition":
        other["item_id"] = "different-edition"
    else:
        other["provider_id"] = "kobo" if provider == "numilog" else "numilog"
    tables["entitlements"].append(other)
    report = reconcile(inventory, EvidenceSnapshot(tables))
    assert report.files[0] == {"confirmed_link"}
    assert len([e for e in report.edges if e.certainty == "attested"]) == 1
    assert report.unverified_rights == [1]
    assert report.reasons == set()
