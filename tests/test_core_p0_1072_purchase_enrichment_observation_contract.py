"""Provider-neutral optional purchase associations are not fetch or right grants."""
from dataclasses import replace

import pytest

from cifereca.plugin_api import (
    ProviderRightsScope, SourceReference, DownloadCapability,
    OptionalRightMetadata, ResourceObservation, ResourceSnapshot,
    PurchaseEnrichmentCandidate, PurchaseEnrichmentObservation,
)

AT = "2026-11-19T08:00:00+00:00"


def inventory(provider="synthetic-one", identity="reader", *, status="partial"):
    scope = ProviderRightsScope(identity, provider, "credential", "account")
    src = SourceReference(scope, "inventory", "edition", "resource", "purchase")
    resource = ResourceObservation(
        src, DownloadCapability("unknown"), "inventory-card", AT,
        OptionalRightMetadata(state="active", right_id="resource"),
    )
    return ResourceSnapshot(
        scope, "inventory", (resource,), status, coverage="partial",
        pagination_complete=False, observed_at=AT, snapshot_ref="same-read",
    ), src


@pytest.mark.parametrize("provider", ["synthetic-one", "synthetic-two"])
def test_exact_candidate_is_private_and_inert_for_any_supplier(provider):
    snapshot, source = inventory(provider)
    candidate = PurchaseEnrichmentCandidate(
        source, "https://catalog.example.org/product/123", "Same title", AT,
    )
    response = PurchaseEnrichmentObservation(snapshot, (candidate,))
    assert response.inventory is snapshot
    assert response.candidates == (candidate,)
    assert not candidate.authorizes_fetch and not response.authorizes_fetch
    assert not snapshot.authorizes_acquisition
    assert "credential" not in repr(response)
    assert "product/123" not in repr(candidate)


@pytest.mark.parametrize("url", [
    "http://catalog.example.org/product/123",
    "https://user:pass@catalog.example.org/product/123",
    "https://catalog.example.org/product/123?trackid=private",
    "https://catalog.example.org/product/123?",
    "https://catalog.example.org/product/123#",
    "https://catalog.example.org/../product",
    "https://catalog.example.org:8443/product/123",
])
def test_invalid_locator_cannot_be_candidate(url):
    _, source = inventory()
    with pytest.raises(ValueError, match="invalid_purchase_enrichment_candidate"):
        PurchaseEnrichmentCandidate(source, url, "Same title", AT)


def test_scope_timestamp_duplicate_and_unattested_purchase_are_refused():
    snapshot, source = inventory()
    candidate = PurchaseEnrichmentCandidate(source, "https://catalog.example.org/book", "Title", AT)
    for foreign in (
        replace(candidate, source=replace(source, scope=replace(source.scope, identity_id="other"))),
        replace(candidate, source=replace(source, edition_id="foreign-edition")),
        replace(candidate, observed_at="2026-11-19T09:00:00+00:00"),
    ):
        with pytest.raises(ValueError, match="unattested_purchase_enrichment_candidate"):
            PurchaseEnrichmentObservation(snapshot, (foreign,))
    with pytest.raises(ValueError, match="unattested_purchase_enrichment_candidate"):
        PurchaseEnrichmentObservation(snapshot, (candidate, candidate))
    inactive = replace(snapshot, resources=(replace(
        snapshot.resources[0], right=OptionalRightMetadata(state="unknown"),
    ),))
    with pytest.raises(ValueError, match="unattested_purchase_enrichment_candidate"):
        PurchaseEnrichmentObservation(inactive, (candidate,))
    assert PurchaseEnrichmentObservation(inactive).candidates == ()
