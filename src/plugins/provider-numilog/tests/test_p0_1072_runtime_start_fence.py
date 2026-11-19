"""Actual Numilog private context and late anonymous transport guard."""
from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace

import pytest

from cifereca.plugin_api import ProviderCapabilityError
from tests.test_p0_1072_public_detail_freshness import session, count
from tests.test_p1_3252_rights_observation_snapshot import session as private_session
from tests.test_public_detail_transport_p0 import URL, AnonymousClient
import cifereca_numilog_public_client as client


@pytest.mark.parametrize("mode", ("valid", "revoked", "missing"))
def test_public_producer_requires_core_start_and_never_holds_it_across_http(monkeypatch, mode):
    s, _, _ = session(monkeypatch)
    held = [False]
    starts = []
    class Transport(AnonymousClient):
        def stream(self, method, url):
            assert held[0] is False
            return super().stream(method, url)
    Transport.requests = AnonymousClient.requests
    monkeypatch.setattr(client.HTTPX_TRANSPORT, "client", Transport)
    def check():
        return True
    @contextmanager
    def start():
        starts.append(True)
        held[0] = True
        try:
            yield mode == "valid"
        finally:
            held[0] = False
    if mode != "missing":
        check.hold_transport_start = start
    control = SimpleNamespace(check_cancelled=lambda: None)
    try:
        if mode == "valid":
            response, at, guard = s.purchase_enrichment_public_response(
                URL, "Fixture", check_authority=check, control=control,
            )
            assert response.results[0].title == "Fixture" and at == 1000
            with guard():
                pass
            s.purchase_enrichment_public_response(
                URL, "Fixture", check_authority=check, control=control,
            )
            assert starts == [True] and count() == 1
        else:
            with pytest.raises(ProviderCapabilityError):
                s.purchase_enrichment_public_response(
                    URL, "Fixture", check_authority=check, control=control,
                )
            assert count() == 0
            assert starts == ([True] if mode == "revoked" else [])
    finally:
        s.close()


def test_private_guard_rejects_recreated_account_and_old_roster():
    s, _ = private_session()
    try:
        account = s._accounts[0]
        account.identity_id = "identity-A"
        assert s.capture_purchase_enrichment_guard(replace(account)) is None
        hold = s.capture_purchase_enrichment_guard(account)
        assert callable(hold)
        with hold() as allowed:
            assert allowed is True
        with s.lock:
            s._accounts = [replace(account)]
        with hold() as allowed:
            assert allowed is False
    finally:
        s.close()
