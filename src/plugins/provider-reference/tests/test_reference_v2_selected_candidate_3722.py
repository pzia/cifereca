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


def _deliver_guarded(session, account, observation, target, admission_current):
    source = observation.source
    return session.downloadable_selected_delivery_candidate_guarded(
        account, edition_id=source.edition_id, resource_id=source.resource_id,
        kind=source.kind, branch=source.branch,
        selected_offer_id=observation.capability.offer_id,
        target_dir=target, explicit_user_action=True,
        admission_current=admission_current,
    )


def test_core_guarded_delivery_succeeds_only_with_live_admission(tmp_path):
    session, (alice, _) = fixture()
    try:
        observation = _selected(session, alice)
        callbacks = []
        def admission_current():
            callbacks.append(True)
            return True
        candidate = _deliver_guarded(
            session, alice, observation, tmp_path, admission_current,
        )
        assert type(candidate) is DeliveryCandidate
        assert candidate.source == observation.source
        assert candidate.authorizes_publication is False
        assert zipfile.is_zipfile(candidate.path)
        # The guarded producer revalidates during staging, not just at entry.
        assert len(callbacks) >= 3
    finally:
        session.close()


@pytest.mark.parametrize("revocation", [
    "before", "before_write", "during_payload", "callback_error", "missing_callback",
])
def test_core_guarded_delivery_rejects_revocation_without_residual_file(
    tmp_path, monkeypatch, revocation,
):
    import cifereca_reference as reference

    session, (alice, _) = fixture()
    try:
        observation = _selected(session, alice)
        state = {"enabled": revocation != "before"}
        if revocation == "before_write":
            session._before_write = lambda: state.update(enabled=False)
        elif revocation == "during_payload":
            original = reference.epub_bytes
            def revoke_as_bytes_are_prepared(resource):
                data = original(resource)
                state["enabled"] = False
                return data
            monkeypatch.setattr(reference, "epub_bytes", revoke_as_bytes_are_prepared)

        def admission_current():
            if revocation == "callback_error":
                raise RuntimeError("injected callback failure")
            return state["enabled"]

        with pytest.raises(
            ProviderCapabilityError, match="reference_core_admission_revoked"
        ):
            _deliver_guarded(
                session, alice, observation, tmp_path,
                None if revocation == "missing_callback" else admission_current,
            )
        # No candidate escapes when OFF is observed even after bytes landed.
        assert list(tmp_path.iterdir()) == []
    finally:
        session.close()
