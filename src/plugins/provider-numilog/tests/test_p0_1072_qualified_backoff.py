"""Actual Numilog provider port converts only qualified transient transport outcomes."""
from types import SimpleNamespace

import pytest

import cifereca_numilog_public_client as client
from cifereca.plugin_api import ProviderError
from cifereca.plugin_api import PurchaseEnrichmentDeferred
from tests.test_public_detail_transport_p0 import URL, AnonymousClient
from tests.test_p0_1072_public_detail_freshness import session


@pytest.mark.parametrize("status, reason", [(429, "rate_limited"), (503, "unavailable"), (403, None)])
def test_real_public_response_port_classifies_only_transient_status(monkeypatch, status, reason):
    s, mono, wall = session(monkeypatch)

    class Failure(AnonymousClient):
        requests = []
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.status = status

        @property
        def headers(self):
            return {"retry-after": "120", "content-type": "text/html"}

    monkeypatch.setattr(client.HTTPX_TRANSPORT, "client", Failure)
    control = SimpleNamespace(check_cancelled=lambda: None)
    try:
        if reason is None:
            with pytest.raises(ProviderError, match="forbidden"):
                s._purchase_enrichment_response(URL, "Fixture", check_authority=lambda: True,
                                                control=control)
        else:
            with pytest.raises(PurchaseEnrichmentDeferred) as caught:
                s._purchase_enrichment_response(URL, "Fixture", check_authority=lambda: True,
                                                control=control)
            assert (caught.value.reason, caught.value.retry_after_seconds) == (reason, 120)
            mono[0] += 10
            with pytest.raises(PurchaseEnrichmentDeferred) as paused:
                s._purchase_enrichment_response(URL, "Fixture", check_authority=lambda: True,
                                                control=control)
            assert paused.value.retry_after_seconds == 110
        assert sum(item[0] == "request" for item in Failure.requests) == 1
    finally:
        s.close()
