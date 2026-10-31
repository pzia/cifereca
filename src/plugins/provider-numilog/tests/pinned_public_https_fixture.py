"""Synthetic Core public-client pinning seam for selected Numilog provider tests.

Network is never opened: production HTTPS pinning is tested separately by
tests/test_public_httpx_transport.py. This fixture proves the provider sends
every external signed GET through the *Core* seam, not self.client.
"""
from __future__ import annotations

import httpx


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
            follow_redirects=False, trust_env=False,
        )

    monkeypatch.setattr(provider.HTTPX_TRANSPORT, "public_client", factory)
    return calls
