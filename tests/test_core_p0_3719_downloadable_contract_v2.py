"""#3719: normative v2 provider-resource ABI and strictly positive v1 bridge."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from cifereca.plugin_api import (
    DOWNLOADABLE_RESOURCE_CONTRACT, SourceReference, DownloadCapability,
    OptionalRightMetadata, ResourceObservation, ResourceSnapshot,
    DeliveryCandidate, from_legacy_rights_snapshot, as_legacy_rights_snapshot,
    ProviderRightsScope, ProviderRightObservation, ProviderRightsSnapshot,
)

WHEN = "2026-10-30T10:00:00+00:00"


@pytest.fixture
def scope():
    return ProviderRightsScope("profile-a", "seller", "credential-a", "buyer-a")


def source(scope, *, resource="resource-1", identity=None):
    if identity:
        scope = ProviderRightsScope(identity, "seller", "credential-a", "buyer-a")
    return SourceReference(scope, "purchases", "edition-1", resource, "purchase")


def observed(scope, *, state="available", right=None, resource="resource-1"):
    return ResourceObservation(
        source(scope, resource=resource),
        DownloadCapability(state, "offer-1" if state == "available" else ""),
        "provider-response-1", WHEN, right,
    )


def snapshot(scope, *resources):
    return ResourceSnapshot(
        scope, "purchases", tuple(resources), "success",
        "complete", True, WHEN, "snapshot-1",
    )


def test_unowned_resource_and_fresh_offer_roundtrip_without_inventing_right(scope):
    item = observed(scope)
    assert item.right is None
    assert item.can_propose_delivery
    assert not item.authorizes_acquisition
    v2 = snapshot(scope, item)
    assert v2.schema_version == DOWNLOADABLE_RESOURCE_CONTRACT
    assert not v2.authorizes_acquisition
    assert not v2.authorizes_negative_reconciliation
    v1 = as_legacy_rights_snapshot(v2)
    assert v1.rights[0].state == "unknown"
    assert v1.rights[0].right_id == "resource-1"
    assert v1.rights[0].delivery == "available"
    assert v1.rights[0].provider_attested
    converted = from_legacy_rights_snapshot(v1)
    assert converted.resources[0].capability.downloadable
    assert converted.resources[0].right.state == "unknown"
    assert converted.resources[0].right.right_id == "resource-1"
    assert converted.resources[0].source == item.source


@pytest.mark.parametrize("state", ["active", "inactive", "unknown"])
def test_right_state_is_descriptive_even_with_available_offer(scope, state):
    item = observed(scope, right=OptionalRightMetadata(state))
    assert item.can_propose_delivery is True
    legacy = as_legacy_rights_snapshot(snapshot(scope, item))
    assert legacy.rights[0].state == state
    assert legacy.rights[0].delivery_attested is True
    assert legacy.rights[0].authorizes_mutation is False


@pytest.mark.parametrize("kind", ["unavailable", "unknown"])
def test_no_offer_never_proposes_delivery(scope, kind):
    item = observed(scope, state=kind, right=OptionalRightMetadata("active"))
    assert item.can_propose_delivery is False
    converted = as_legacy_rights_snapshot(snapshot(scope, item))
    assert converted.rights[0].delivery_offer_id == ""
    assert converted.rights[0].delivery_attested is False


@pytest.mark.parametrize("state", ["active", "inactive", "unknown"])
def test_archived_is_explicit_negative_delivery_not_legal_right(scope, state):
    item = observed(scope, right=OptionalRightMetadata(state))
    item = replace(item, capability=DownloadCapability("unavailable", "", "archived"))
    assert item.can_propose_delivery is False
    legacy = as_legacy_rights_snapshot(snapshot(scope, item))
    assert legacy.rights[0].delivery_unavailable_reason == "archived"
    assert legacy.rights[0].state == state


def test_legacy_unattested_rows_cannot_promote_to_v2_source_or_full_snapshot(scope):
    v1 = ProviderRightsSnapshot(
        scope,
        rights=(
            ProviderRightObservation(
                "edition-1", "resource-1", "purchase", "unknown",
                "provider_response", "response", WHEN,
                "available", "offer-1",
            ),
            ProviderRightObservation(
                "edition-2", "resource-2", "purchase", "active",
                "legacy_unverified",
            ),
        ),
        status="success", coverage="complete", pagination_complete=True,
        branch="purchases", observed_at=WHEN, snapshot_ref="full-v1",
    )
    v2 = from_legacy_rights_snapshot(v1)
    assert len(v2.resources) == 1
    assert v2.resources[0].source.resource_id == "resource-1"
    assert v2.coverage == "unknown"
    assert v2.pagination_complete is False
    assert not v2.authorizes_negative_reconciliation


def test_failed_empty_legacy_snapshot_preserves_branch_without_invention(scope):
    v1 = ProviderRightsSnapshot(scope, status="failed")
    v2 = from_legacy_rights_snapshot(v1)
    assert v2.branch == ""
    assert v2.resources == ()
    assert as_legacy_rights_snapshot(v2) == v1


def test_hundreds_of_resources_keep_exact_identity_and_no_implicit_consent(scope):
    resources = tuple(observed(scope, resource=f"resource-{i}") for i in range(351))
    result = snapshot(scope, *resources)
    assert len(result.resources) == 351
    assert len(as_legacy_rights_snapshot(result).rights) == 351
    assert not result.authorizes_acquisition
    assert all(not r.authorizes_acquisition for r in result.resources)


def test_same_resource_id_in_other_account_remains_separate(scope):
    original = observed(scope)
    other_scope = ProviderRightsScope("profile-b", "seller", "credential-b", "buyer-b")
    other = observed(other_scope)
    assert original.source != other.source
    assert snapshot(scope, original).resources[0].source.scope == scope
    assert snapshot(other_scope, other).resources[0].source.scope == other_scope
    with pytest.raises(ValueError, match="scope_mismatch"):
        snapshot(scope, other)


def test_duplicate_resource_tuple_rejected(scope):
    with pytest.raises(ValueError, match="ambiguous_downloadable_resources"):
        snapshot(scope, observed(scope), observed(scope))


@pytest.mark.parametrize("broken", [
    DownloadCapability("unknown"),
    DownloadCapability("unavailable"),
])
def test_right_is_optional_for_unavailable_source(scope, broken):
    row = replace(observed(scope), capability=broken, right=None)
    assert row.right is None
    assert not row.can_propose_delivery


@pytest.mark.parametrize("state,offer,reason", [
    ("available", "", ""),
    ("unknown", "signed-offer", ""),
    ("unavailable", "signed-offer", ""),
    ("available", "signed-offer", "archived"),
    ("unknown", "", "archived"),
])
def test_capacity_cannot_forge_signed_offer(state, offer, reason):
    with pytest.raises(ValueError, match="invalid_download_capability"):
        DownloadCapability(state, offer, reason)


def test_candidate_is_only_a_plugin_staging_claim(scope):
    candidate = DeliveryCandidate(
        source(scope), "signed-offer", Path("/private/staging/candidate.epub"),
        "opaque-candidate-id",
    )
    assert candidate.selected_offer_id == "signed-offer"
    assert candidate.authorizes_publication is False
    with pytest.raises(ValueError, match="invalid_delivery_candidate"):
        DeliveryCandidate(source(scope), "", Path("relative.epub"))


@pytest.mark.parametrize("state", ["failed", "cancelled"])
def test_failed_or_cancelled_inventory_can_never_contain_resources(scope, state):
    with pytest.raises(ValueError, match="invalid_failed_downloadable_snapshot"):
        replace(snapshot(scope, observed(scope)), status=state, coverage="unknown")


def test_partial_inventory_does_not_imply_missing_resources(scope):
    item = observed(scope)
    partial = ResourceSnapshot(
        scope, "purchases", (item,), "partial", "partial",
        False, WHEN, "partial", "cursor-next",
    )
    assert not partial.authorizes_negative_reconciliation
    as_legacy = as_legacy_rights_snapshot(partial)
    assert as_legacy.status == "partial"
    assert as_legacy.resume_cursor == "cursor-next"


def test_complete_claim_needs_exact_proof(scope):
    with pytest.raises(ValueError, match="unproven_downloadable_completeness"):
        replace(snapshot(scope, observed(scope)), pagination_complete=False)
    with pytest.raises(ValueError, match="unproven_downloadable_completeness"):
        replace(snapshot(scope, observed(scope)), snapshot_ref="")


@pytest.mark.parametrize("field,value", [
    ("evidence_ref", ""), ("evidence_ref", "bad\ntrace"),
    ("observed_at", "not-a-date"),
])
def test_unattested_or_unbounded_resource_evidence_is_rejected(scope, field, value):
    with pytest.raises(ValueError, match="invalid_downloadable_observation"):
        replace(observed(scope), **{field: value})


def test_source_ids_are_exact_and_right_metadata_is_not_a_source_key(scope):
    item = observed(scope, right=OptionalRightMetadata(
        "inactive", right_id="purchase-entitlement-123",
    ))
    assert item.source.resource_id == "resource-1"
    v1 = as_legacy_rights_snapshot(snapshot(scope, item))
    # The legacy slot carries a stable resource key, not a made-up grant.
    assert v1.rights[0].right_id == "resource-1"
    assert v1.rights[0].state == "inactive"


# The versioned v2 observation is accepted by the EXISTING source-Sync
# reader, without a second loop, provider-name dispatch or new consent.
from cifereca.core.rights_snapshot_read import read_account_rights_snapshot
from cifereca.core.sync_coordination import synchronize_account_source
from tests.test_core_p0_3171_canonical_sync_stage import rig, refs


def test_real_core_sync_accepts_v2_from_existing_typed_reader_once(rig):
    service, db, _root = rig
    v2 = from_legacy_rights_snapshot(service.snapshot)
    resource = replace(
        v2.resources[0], right=None,
        capability=DownloadCapability("available", "signed-resource-offer"),
    )
    v2 = replace(v2, resources=(resource,))
    # Install the *existing* Core executor route but deny all source consent.
    # A positive provider offer alone must never call the plugin for bytes.
    service._canonical_persisted_consent = lambda _account: frozenset()
    service._canonical_right_delivery = lambda *_args: pytest.fail(
        "v2 offer without persisted consent cannot GET"
    )
    observed = []
    service.session.rights_observation_snapshot = lambda account: (
        observed.append(account) or v2
    )
    result = synchronize_account_source(service, service.account)
    assert observed == [service.account]
    assert result["canonical_rights"]["observed"] == 1
    assert result["canonical_rights"]["source_references_changed"] == 1
    assert result["canonical_execution"]["downloaded"] == 0
    assert result["canonical_execution"]["pending"] == 1
    assert refs(db)[0][0][0:4] == ("A", "seller-a", "card-a", "buyer-a")
    assert db.placements() == [] and db.entitlements() == []


def test_real_core_reader_rejects_v2_cross_account_binding(rig):
    service, db, _root = rig
    foreign = ProviderRightsScope("other-profile", "seller-a", "card-a", "buyer-a")
    v2 = snapshot(foreign, observed(foreign))
    service.session.rights_observation_snapshot = lambda _account: v2
    result = read_account_rights_snapshot(service, service.account)
    assert result.status == "failed"
    assert result.rights == ()
    assert refs(db) == ([], 0)


def test_real_core_reader_drops_v2_if_upstream_rebinds_during_read(rig):
    service, db, _root = rig
    v2 = from_legacy_rights_snapshot(service.snapshot)

    def switched(_account):
        service._source_context_epoch += 1
        return v2

    service.session.rights_observation_snapshot = switched
    result = read_account_rights_snapshot(service, service.account)
    assert result.status == "failed"
    assert result.rights == ()
    assert refs(db) == ([], 0)
