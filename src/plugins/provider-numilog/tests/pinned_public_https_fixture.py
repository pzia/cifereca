"""Synthetic Core public-client pinning seam for selected Numilog provider tests.

Network is never opened: production HTTPS pinning is tested separately by
tests/test_public_httpx_transport.py. This fixture proves the provider sends
every external signed GET through the *Core* seam, not self.client.
"""
from __future__ import annotations

import httpx
from cifereca.http_trace import BROWSER_USER_AGENT


def mock_selected_public_client(monkeypatch, provider, handler):
    calls = []

    def factory(*, producer, timeout):
        assert producer == "provider.numilog.delivery"
        assert timeout == 25
        seen = []
        calls.append((producer, timeout, seen))

        def traced(request):
            seen.append(str(request.url))
            return handler(request)

        return httpx.Client(
            transport=httpx.MockTransport(traced),
            headers={"User-Agent": BROWSER_USER_AGENT},
            follow_redirects=False, trust_env=False,
        )

    monkeypatch.setattr(provider.HTTPX_TRANSPORT, "public_client", factory)
    return calls


def mock_selected_public_client_from_session(monkeypatch, provider, session):
    """Replay the legacy test's mocked transport via the Core public seam.

    This is test-only and preserves the exact synthetic response script while
    proving that a selected external GET cannot use session.client.
    """
    transport = session.client._transport
    assert isinstance(transport, httpx.MockTransport)
    return mock_selected_public_client(
        monkeypatch, provider, transport.handle_request,
    )
