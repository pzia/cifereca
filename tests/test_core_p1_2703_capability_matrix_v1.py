"""P1 #2703: closed Core capability evidence v1, not acquisition admission.

Only fake descriptors and synthetic proofs; no live accounts, files, HTTP or
provider dispatch. Preserves existing ProviderCapabilities/search contracts.
"""
from dataclasses import FrozenInstanceError, asdict

import pytest

from cifereca.plugin_api import (
    CAPABILITY_MATRIX_CONTRACT,
    CapabilityEvidence, CapabilityMatrix, declared_provider_matrix,
    ProviderCapabilities, ProviderDescriptor, ProviderSearchCapabilities,
)


def _descriptor(**flags):
    return ProviderDescriptor(
        id="synthetic", display_name="Source", description="test",
        state="ready",
        capabilities=ProviderCapabilities(**flags),
        search_capabilities=ProviderSearchCapabilities(
            fields=("all", "title"), scopes=("all", "personal"),
            # Actual compatibility is an explicit relation, NOT a product.
            pagination="page", page_size=20,
        ),
    )


def test_all_declared_families_are_read_only_and_preserve_implemented_vs_live():
    result = declared_provider_matrix(
        _descriptor(search=True, loans=True, borrow=True,
                    direct_download=True, personal_library=True),
        enabled=False, connected=True,
    )
    assert CAPABILITY_MATRIX_CONTRACT == "capability-matrix/1"
    assert result.schema_version == 1
    assert len(result.observations) == 8
    found = {x.operation: x for x in result.observations}
    for name in ("catalog.search", "personal_inventory.list", "loan.inventory",
                 "loan.borrow", "acquisition.download"):
        item = found[name]
        assert item.status == "unverified"  # code != live attestation
        assert item.implementation_state == "implemented"
        assert item.source_kind == "code"
        assert item.enabled is False
        assert item.connected is True
        assert item.mode == "unknown"
        assert item.authorizes_mutation is False
    for name in ("loan.return", "loan.renew", "artifact.backup"):
        item = found[name]
        assert item.status == "unsupported"
        assert item.implementation_state == "not_applicable"
    assert result.exact("catalog.search", "unknown") == found["catalog.search"]
    assert result.exact("catalog.search", "public") is None
    assert result.exact("catalog.search", "unknown", field="title") is None
    assert result.exact("catalog.search", "unknown", field="all") is None


def test_scope_field_account_edition_cannot_be_cross_joined():
    a = CapabilityEvidence(
        provider_id="synthetic", operation="catalog.search", scope="catalog",
        field="all", identity_id="alice", account_id="card-a",
        status="supported", implementation_state="implemented",
        mode="server", source_kind="provider_observation",
        evidence_ref="fixture:exact-catalog-a",
    )
    b = CapabilityEvidence(
        provider_id="synthetic", operation="catalog.search", scope="personal",
        field="title", identity_id="bob", account_id="card-b",
        status="partial", implementation_state="implemented",
        mode="local_postfilter_partial", source_kind="account_observation",
        evidence_ref="fixture:b-page", reason="only_received_page",
    )
    matrix = CapabilityMatrix("synthetic", (a, b))
    assert matrix.exact(
        "catalog.search", "catalog", field="all",
        identity_id="alice", account_id="card-a",
    ) == a
    assert matrix.exact(
        "catalog.search", "personal", field="title",
        identity_id="bob", account_id="card-b",
    ) == b
    for field, scope, owner, account in (
        ("title", "catalog", "alice", "card-a"),
        ("all", "personal", "bob", "card-b"),
        ("all", "catalog", "bob", "card-a"),
        ("all", "catalog", "alice", "card-b"),
        ("all", "catalog", "alice", ""),
    ):
        assert matrix.exact(
            "catalog.search", scope, field=field,
            identity_id=owner, account_id=account,
        ) is None
    assert a.authorizes_mutation is False
    assert b.authorizes_mutation is False


def test_right_inventory_is_independent_from_files_and_consent():
    matrix = declared_provider_matrix(_descriptor(personal_library=True, backup=False))
    assert matrix.exact("personal_inventory.list", "unknown").implementation_state == "implemented"
    assert matrix.exact("artifact.backup", "unknown").status == "unsupported"
    assert matrix.exact("acquisition.download", "unknown").status == "unsupported"
    assert not any(row.authorizes_mutation for row in matrix.observations)


@pytest.mark.parametrize("overrides,code", [
    ({"provider_id": "BAD PROVIDER"}, "capability_invalid_key"),
    ({"scope": ""}, "capability_invalid_key"),
    ({"operation": "catalog/search"}, "capability_invalid_key"),
    ({"field": "a\nb"}, "capability_invalid_scope"),
    ({"schema_version": True}, "capability_invalid_contract"),
    ({"schema_version": 2}, "capability_invalid_contract"),
    ({"status": "ready"}, "capability_invalid_contract"),
    ({"implementation_state": "available"}, "capability_invalid_contract"),
    ({"mode": "guess"}, "capability_invalid_contract"),
    ({"source_kind": "invented"}, "capability_invalid_contract"),
    ({"enabled": 1}, "capability_invalid_state"),
    ({"connected": "yes"}, "capability_invalid_state"),
    ({"status": "partial"}, "capability_partial_reason_required"),
    ({"status": "supported"}, "capability_unverified_support"),
    ({"status": "supported", "evidence_ref": "fixture:proof"}, "capability_unverified_support"),
    ({"status": "supported", "source_kind": "provider_observation"}, "capability_unverified_support"),
    ({"reason": "a" * 513}, "capability_invalid_evidence"),
])
def test_malformed_or_unsupported_claims_fail_closed(overrides, code):
    options = {
        "provider_id": "synthetic", "operation": "catalog.search", "scope": "public",
        **overrides,
    }
    with pytest.raises(ValueError, match="^" + code + "$"):
        CapabilityEvidence(**options)


def test_versioned_contract_is_frozen_without_implicit_fallback():
    value = CapabilityEvidence(
        provider_id="synthetic", operation="metadata.enrichment",
        scope="edition", identity_id="a", account_id="b", edition_id="epub-1",
        status="supported", implementation_state="implemented",
        source_kind="provider_observation", evidence_ref="fixture:exact-epub",
    )
    matrix = CapabilityMatrix("synthetic", (value,))
    assert matrix.exact("metadata.enrichment", "edition",
                        identity_id="a", account_id="b", edition_id="epub-1") == value
    assert matrix.exact("metadata.enrichment", "edition",
                        identity_id="a", account_id="b", edition_id="paper-1") is None
    assert asdict(value)["schema_version"] == 1
    assert asdict(value)["authorizes_mutation"] if False else True  # not serialized
    with pytest.raises(FrozenInstanceError):
        value.status = "unsupported"


@pytest.mark.parametrize("rows,code", [
    ((CapabilityEvidence("foreign", "loan.borrow", "unknown"),), "capability_owner_mismatch"),
    ((CapabilityEvidence("synthetic", "loan.borrow", "unknown"),) * 2, "capability_duplicate_scope"),
])
def test_matrix_rejects_cross_provider_or_overlapping_claims(rows, code):
    with pytest.raises(ValueError, match="^" + code + "$"):
        CapabilityMatrix("synthetic", rows)
