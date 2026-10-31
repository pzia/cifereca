"""Provider reference: v2 selected-candidate CI safety regression (#3722)."""
from pathlib import Path
import zipfile
import pytest

from cifereca.plugin_api import DeliveryCandidate, ProviderCapabilityError
from cifereca_reference import BRANCH
from tests.test_reference_provider import fixture


def _selected(session, account, edition="epub-002"):
    result = session.downloadable_selected_offer_snapshot(
        account, edition_id=edition, resource_id="right-" + edition,
        kind="purchase", branch=BRANCH, explicit_user_action=True,
    )
    return result.resources[0]


def _deliver(session, account, observation, target):
    source = observation.source
    return session.downloadable_selected_delivery_candidate(
        account, edition_id=source.edition_id, resource_id=source.resource_id,
        kind=source.kind, branch=source.branch,
        selected_offer_id=observation.capability.offer_id,
        target_dir=target, explicit_user_action=True,
    )


def test_selected_v2_candidate_is_exact_and_has_no_publish_authority(tmp_path):
    session, (alice, bob) = fixture()
    try:
        selected = _selected(session, alice)
        assert selected.right is None
        assert selected.capability.downloadable
        result = _deliver(session, alice, selected, tmp_path)
        assert type(result) is DeliveryCandidate
        assert result.source == selected.source
        assert result.selected_offer_id == selected.capability.offer_id
        assert result.path.parent == tmp_path
        assert zipfile.is_zipfile(result.path)
        assert result.authorizes_publication is False
    finally:
        session.close()


def test_v2_selected_offer_is_account_scoped_before_staging(tmp_path):
    session, (alice, bob) = fixture()
    try:
        selected = _selected(session, alice)
        with pytest.raises(ProviderCapabilityError):
            _deliver(session, bob, selected, tmp_path)
        assert not list(tmp_path.iterdir())
    finally:
        session.close()


def test_v2_archived_and_unknown_never_stage(tmp_path):
    session, (alice, _) = fixture()
    try:
        for edition in ("archived-006", "no-offer-004"):
            selected = _selected(session, alice, edition)
            assert not selected.capability.downloadable
            assert selected.capability.offer_id == ""
            with pytest.raises(ProviderCapabilityError):
                _deliver(session, alice, selected, tmp_path)
        assert not list(tmp_path.iterdir())
    finally:
        session.close()
