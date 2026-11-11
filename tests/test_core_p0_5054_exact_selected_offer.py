"""P0 #5054: exact offer is provenance+availability, never stored rights."""
from dataclasses import replace

import pytest

from cifereca.core.explicit_selected_offer import exact_selected_offer_id
from cifereca.core.source_convergence import SourceKey
from cifereca.plugin_api.downloadable_resources import (
    SourceReference, DownloadCapability, ResourceObservation, ResourceSnapshot,
    OptionalRightMetadata,
)
from cifereca.plugin_api.provider_rights_observation import ProviderRightsScope


def _fixture(*, credential="credential-a", item="edition-a", status="success",
             state="available", legal=None):
    scope = ProviderRightsScope("owner", "numilog", credential, "account")
    source = SourceReference(scope, "meslivres", item, item, "purchase")
    offer = ResourceObservation(
        source=source, capability=DownloadCapability(
            state, "offer:one" if state == "available" else "",
        ),
        evidence_ref="private-observation", observed_at="2026-11-11T00:00:00Z",
        right=legal,
    )
    snapshot = ResourceSnapshot(
        scope=scope, branch="meslivres", status=status, resources=(offer,),
    )
    key = SourceKey(scope, item, item, "purchase", "meslivres")
    return snapshot, key


def test_current_offer_is_accepted_without_legal_right_metadata():
    snap, key = _fixture(legal=None)
    assert exact_selected_offer_id(snap, key) == "offer:one"


@pytest.mark.parametrize("right_state", ["active", "inactive", "unknown"])
def test_legal_right_state_is_not_a_second_download_permission(right_state):
    snap, key = _fixture(legal=OptionalRightMetadata(state=right_state))
    assert exact_selected_offer_id(snap, key) == "offer:one"


@pytest.mark.parametrize("state", ["unavailable", "unknown"])
def test_absent_current_download_capability_is_refused(state):
    snap, key = _fixture(state=state)
    assert exact_selected_offer_id(snap, key) is None


def test_wrong_account_or_edition_is_refused_even_with_available_offer():
    snap, key = _fixture()
    _, foreign_account = _fixture(credential="foreign-credential")
    _, foreign_edition = _fixture(item="another-edition")
    assert exact_selected_offer_id(snap, foreign_account) is None
    assert exact_selected_offer_id(snap, foreign_edition) is None


def test_failed_snapshot_mixed_batch_and_untyped_inputs_are_refused():
    snap, key = _fixture()
    assert exact_selected_offer_id(None, key) is None
    assert exact_selected_offer_id(snap, object()) is None
    assert exact_selected_offer_id(replace(snap, resources=()), key) is None
    # The transport contract itself rejects duplicate exact resource keys.
    with pytest.raises(ValueError, match="duplicate"):
        replace(snap, resources=(snap.resources[0], snap.resources[0]))
    assert exact_selected_offer_id(replace(snap, status="failed", resources=()), key) is None
