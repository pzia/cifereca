"""Actual Vault requests use Core capture without extra auth or requests."""
import httpx
import cifereca_kobo_vault as vault
from cifereca.http_trace import BROWSER_USER_AGENT, HTTPTraceCapture
import cifereca.http_trace as trace_module
from cifereca.logging_utils import diagnostics_level, set_diagnostics_level


def test_vault_service_preserves_anonymous_and_authenticated_requests(monkeypatch):
    capture=HTTPTraceCapture()
    monkeypatch.setattr(trace_module,'HTTP_TRACE',capture)
    previous=diagnostics_level()
    original=vault.HTTPX_TRANSPORT.client
    constructions,requests=[],[]
    content=b'{"synthetic":"value"}'

    def serve(request):
        requests.append(request)
        assert request.headers['user-agent'] == BROWSER_USER_AGENT
        return httpx.Response(200,headers={'Content-Type':'application/json'},stream=httpx.ByteStream(content))

    def construct(**options):
        constructions.append(options.copy())
        return original(**options)

    monkeypatch.setattr(vault.HTTPX_TRANSPORT,'client',construct)
    set_diagnostics_level('normal')
    client=vault.KoboVaultClient('https://vault.example.test','synthetic-token',transport=httpx.MockTransport(serve))
    try:
        assert client.status()=={'synthetic':'value'}
        set_diagnostics_level('trace')
        assert client.account()=={'synthetic':'value'}
        set_diagnostics_level('normal')
        assert client.status()=={'synthetic':'value'}
        assert len(constructions)==1 and len(requests)==3
        assert constructions[0]['producer']=='provider.kobo'
        assert constructions[0]['follow_redirects'] is False
        assert requests[0].headers.get('Authorization') is None
        assert requests[1].headers['Authorization']=='Bearer synthetic-token'
        assert requests[2].headers.get('Authorization') is None
        entries=capture.snapshot()['exchanges']
        assert len(entries)==1 and entries[0]['response_body']['data']==content
        assert entries[0]['response_body']['state']=='complete'
    finally:
        client.close()
        set_diagnostics_level(previous)
