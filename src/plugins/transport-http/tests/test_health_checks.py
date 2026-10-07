from __future__ import annotations

from cifereca.plugin_api import DiagnosticsCheckProvider

from cifereca_http_transport_health import HttpTransportHealthChecks


class Transport:
    timeout_seconds = 30.0
    max_download_bytes = 1024 * 1024
    max_redirects = 8


class Context:
    def __init__(self, transport):
        self.transport = transport

    def service(self, plugin_id):
        assert plugin_id == "transport.http"
        return self.transport


def test_transport_health_provider_contract_and_safe_shape():
    provider = HttpTransportHealthChecks().activate(Context(Transport()))
    assert isinstance(provider, DiagnosticsCheckProvider)
    row = provider.checks()[0]
    assert row.status == "ok"
    assert row.detail == {
        "available": True,
        "timeout_seconds": 30.0,
        "max_download_bytes": 1024 * 1024,
        "max_redirects": 8,
    }


def test_transport_health_reports_invalid_limits_without_network():
    transport = Transport()
    transport.max_redirects = -1
    provider = HttpTransportHealthChecks().activate(Context(transport))
    row = provider.checks()[0]
    assert row.status == "error"
    assert row.detail["available"] is True
