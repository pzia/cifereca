"""#3252/#3722 contract probes: a provider-independent deterministic fixture.

No network, Core persistence, consent decisions, or physical publication.
The fixture intentionally uses the public v2 API, not a Numilog-specific path.
"""
from pathlib import Path

import pytest

from cifereca.plugin_api.downloadable_resources import (
    DOWNLOADABLE_RESOURCE_CONTRACT, DeliveryCandidate, DownloadCapability,
    OptionalRightMetadata, ResourceObservation, ResourceSnapshot,
    SourceReference, as_legacy_rights_snapshot, from_legacy_rights_snapshot,
)
from cifereca.plugin_api.provider_rights_observation import ProviderRightsScope

TIMESTAMP = "2026-10-31T08:00:00+00:00"


class FictionalProvider:
    """Synthetic reference adapter for the v2 envelope and exact delivery."""

    def __init__(self, identity="alice", account="account-a"):
        self.scope = ProviderRightsScope(identity, "fictional", "credential-a", account)
        self.resources = {
            "available": ("available", "fixture-offer-1", ""),
            "archived": ("unavailable", "", "archived"),
            "unknown": ("unknown", "", ""),
        }

    def snapshot(self):
        rows = tuple(
            ResourceObservation(
                source=SourceReference(self.scope, "owned", key, key, "purchase"),
                capability=DownloadCapability(*ability),
                evidence_ref="fixture-" + key,
                observed_at=TIMESTAMP,
                right=OptionalRightMetadata(state="unknown"),
            )
            for key, ability in self.resources.items()
        )
        return ResourceSnapshot(
            scope=self.scope, branch="owned", resources=rows,
            status="success", coverage="complete", pagination_complete=True,
            observed_at=TIMESTAMP, snapshot_ref="fixture-snapshot-1",
        )

    def offer(self, source):
        if source.scope != self.scope or source.branch != "owned":
            raise ValueError("fixture_scope_mismatch")
        found = [row for row in self.snapshot().resources if row.source == source]
        if len(found) != 1 or not found[0].capability.downloadable:
            raise ValueError("fixture_offer_unavailable")
        return found[0].capability.offer_id

    def candidate(self, source, offer_id, target_dir):
        if self.offer(source) != offer_id:
            raise ValueError("fixture_offer_changed")
        folder = Path(target_dir)
        if not folder.is_dir() or folder.is_symlink():
            raise ValueError("fixture_staging_invalid")
        path = folder / "fixture.epub"
        path.write_bytes(b"synthetic fixture bytes; not a real EPUB")
        return DeliveryCandidate(source=source, selected_offer_id=offer_id, path=path)


def test_v2_fixture_is_deterministic_and_has_0_1_n_exact_offers():
    fixture = FictionalProvider()
    first = fixture.snapshot()
    assert first == fixture.snapshot()
    assert first.schema_version == DOWNLOADABLE_RESOURCE_CONTRACT
    assert len(first.resources) == 3
    assert [r.capability.state for r in first.resources] == [
        "available", "unavailable", "unknown",
    ]
    assert [r.capability.downloadable for r in first.resources] == [
        True, False, False,
    ]
    assert all(not r.authorizes_acquisition for r in first.resources)
    assert not first.authorizes_acquisition
    assert not first.authorizes_negative_reconciliation


def test_v2_fixture_positive_delivery_with_unknown_right_is_not_permission(tmp_path):
    fixture = FictionalProvider()
    row = fixture.snapshot().resources[0]
    assert row.right.state == "unknown"
    offer = fixture.offer(row.source)
    candidate = fixture.candidate(row.source, offer, tmp_path)
    assert candidate.path.is_file()
    assert candidate.selected_offer_id == offer
    assert not candidate.authorizes_publication


@pytest.mark.parametrize("index", [1, 2])
def test_v2_fixture_negative_or_archived_never_stages(tmp_path, index):
    fixture = FictionalProvider()
    row = fixture.snapshot().resources[index]
    with pytest.raises(ValueError, match="fixture_offer_unavailable"):
        fixture.offer(row.source)
    assert not list(tmp_path.iterdir())


def test_v2_fixture_prevents_cross_identity_and_account(tmp_path):
    alice = FictionalProvider()
    source = alice.snapshot().resources[0].source
    for changed in [FictionalProvider("bob"), FictionalProvider(account="account-b")]:
        with pytest.raises(ValueError, match="fixture_scope_mismatch"):
            changed.offer(source)
        with pytest.raises(ValueError, match="fixture_scope_mismatch"):
            changed.candidate(source, "fixture-offer-1", tmp_path)
    assert not list(tmp_path.iterdir())


def test_v2_fixture_rejects_stale_offer_before_file_creation(tmp_path):
    fixture = FictionalProvider()
    source = fixture.snapshot().resources[0].source
    with pytest.raises(ValueError, match="fixture_offer_changed"):
        fixture.candidate(source, "stale-offer", tmp_path)
    assert not list(tmp_path.iterdir())


def test_v2_fixture_roundtrips_existing_core_compatibility_without_grants():
    snapshot = FictionalProvider().snapshot()
    legacy = as_legacy_rights_snapshot(snapshot)
    assert len(legacy.rights) == 3
    assert legacy.rights[0].state == "unknown"
    assert legacy.rights[0].delivery == "available"
    assert all(not row.authorizes_mutation for row in legacy.rights)
    restored = from_legacy_rights_snapshot(legacy)
    assert restored == snapshot
