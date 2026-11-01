"""P0 #3848/#3171: Numilog's selected supplier GET uses live Core consent.

Synthetic MesLivres and Core HTTPX MockTransport only; no real account,
network acquisition, ADEPT fulfillment or SQLite publication.
"""
from pathlib import Path

import httpx
import pytest

import cifereca_numilog as provider
from cifereca.plugin_api import ProviderCapabilityError
from tests.pinned_public_https_fixture import mock_selected_public_client
from tests.test_p1_3252_selected_delivery_candidate import setup, selected
from tests.test_numilog_owned_js_delivery_p0 import SIGNED

PUBLISHER = "https://public-delivery.test/selected/acsm"


def guarded(session, account, right, tmp_path, current):
    return session.downloadable_selected_delivery_candidate_guarded(
        account, edition_id=right.edition_id, resource_id=right.right_id,
        kind="purchase", branch="meslivres",
        selected_offer_id=right.delivery_offer_id,
        target_dir=tmp_path, explicit_user_action=True,
        admission_current=current,
    )


@pytest.mark.parametrize("denied", [False, None, 1, "yes", "exception"])
def test_revoked_before_selected_action_does_not_read_private_meslivres(
    tmp_path, denied,
):
    session, account, _broker, reads, right = setup()
    try:
        before = list(reads)
        def current():
            if denied == "exception":
                raise RuntimeError("synthetic expired source")
            return denied
        with pytest.raises(ProviderCapabilityError, match="numilog_candidate_admission_changed"):
            guarded(session, account, right, tmp_path, current)
        assert reads == before and list(tmp_path.iterdir()) == []
    finally:
        session.close()


def test_guarded_v2_routes_exact_core_closure_through_selected_transport(tmp_path):
    session, account, _broker, reads, right = setup()
    try:
        captured = []
        path = tmp_path / "only-source-owned.acsm"
        path.write_bytes(b"synthetic-candidate")
        def current():
            return True
        def selected_stage(*_args, **kwargs):
            captured.append(kwargs)
            return path, session.library_url
        session._download = selected_stage
        result = guarded(session, account, right, tmp_path, current)
        assert len(captured) == 1
        assert captured[0]["core_admission_current"] is current
        assert captured[0]["stage_acsm_only"] is True
        assert result.path == path and result.source.scope.identity_id == account.identity_id
        assert result.selected_offer_id == right.delivery_offer_id
        assert not result.authorizes_publication
    finally:
        session.close()


def test_core_revocation_after_signed_redirect_refuses_followup_get(
    tmp_path, monkeypatch,
):
    session, account, _broker, _reads, right = setup()
    source_on = [True]
    requests = []
    def handler(req):
        requests.append(str(req.url))
        assert str(req.url) == SIGNED
        source_on[0] = False
        return httpx.Response(302, headers={"location": PUBLISHER})
    mock_selected_public_client(monkeypatch, provider, handler)
    try:
        with pytest.raises(ProviderCapabilityError):
            guarded(session, account, right, tmp_path, lambda: source_on[0])
        assert requests == [SIGNED]
        assert list(tmp_path.iterdir()) == []
    finally:
        session.close()


def test_off_on_revision_aba_never_restarts_signed_get(tmp_path, monkeypatch):
    session, account, _broker, _reads, right = setup()
    revision = [1]
    original = revision[0]
    requested = []
    def handler(req):
        requested.append(str(req.url))
        assert str(req.url) == SIGNED
        revision[0] += 1  # OFF
        revision[0] += 1  # ON, but old callback stays revoked
        return httpx.Response(307, headers={"location": PUBLISHER})
    mock_selected_public_client(monkeypatch, provider, handler)
    try:
        with pytest.raises(ProviderCapabilityError):
            guarded(session, account, right, tmp_path, lambda: revision[0] == original)
        assert requested == [SIGNED]
        assert list(tmp_path.iterdir()) == []
    finally:
        session.close()


def test_stream_midbody_revocation_discards_unpublished_bytes(
    tmp_path, monkeypatch,
):
    from tests.test_numilog_signed_acsm_transport_p0 import ACSM
    session, account, _broker, _reads, right = setup()
    allowed = [True]
    requests = []
    class RevokedBody(httpx.SyncByteStream):
        def __iter__(self):
            yield ACSM[:12]
            allowed[0] = False
            yield ACSM[12:]
    def handler(req):
        requests.append(str(req.url))
        assert str(req.url) == SIGNED
        return httpx.Response(
            200, stream=RevokedBody(),
            headers={"content-type": "application/vnd.adobe.adept+xml"},
        )
    mock_selected_public_client(monkeypatch, provider, handler)
    try:
        with pytest.raises(ProviderCapabilityError):
            guarded(session, account, right, tmp_path, lambda: allowed[0])
        assert requests == [SIGNED]
        assert list(tmp_path.iterdir()) == []
    finally:
        session.close()
