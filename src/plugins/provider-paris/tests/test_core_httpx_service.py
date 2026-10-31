"""Default Paris construction and compatibility reset use the Core service."""
import httpx
import cifereca_paris_client as paris
from cifereca.http_trace import BROWSER_USER_AGENT, HTTPTraceCapture
import cifereca.http_trace as trace_module
from cifereca.logging_utils import diagnostics_level, set_diagnostics_level


def test_paris_core_client_and_compatibility_reset_preserve_policy(monkeypatch):
    capture=HTTPTraceCapture()
    monkeypatch.setattr(trace_module,'HTTP_TRACE',capture)
    previous=diagnostics_level()
    original=paris.HTTPX_TRANSPORT.client
    constructions,requests=[],[]

    def serve(request):
        requests.append(request)
        assert request.headers['user-agent'] == BROWSER_USER_AGENT
        return httpx.Response(200,headers={'Set-Cookie':'session=synthetic; Path=/'},
                              stream=httpx.ByteStream(b'synthetic HTML'))

    def construct(**options):
        constructions.append(options.copy())
        return original(**options,transport=httpx.MockTransport(serve))

    monkeypatch.setattr(paris.HTTPX_TRANSPORT,'client',construct)
    set_diagnostics_level('normal')
    client=paris.ParisClient('https://example.test')
    try:
        for level in ('normal','trace','normal'):
            set_diagnostics_level(level)
            assert client.client.get('https://example.test/synthetic').content==b'synthetic HTML'
        assert len(constructions)==1 and len(requests)==3
        assert constructions[0]['producer']=='provider.paris'
        assert constructions[0]['follow_redirects'] is True
        assert constructions[0]['limits'].max_connections==20
        assert constructions[0]['limits'].max_keepalive_connections==10
        assert constructions[0]['headers']['Accept-Encoding']=='gzip, deflate, br'
        assert len(capture.snapshot()['exchanges'])==1
        client._reset_http_client('compat')
        assert len(constructions)==2 and len(requests)==3
        assert constructions[1]['headers']['Accept-Encoding']=='identity'
        assert constructions[1]['headers']['Connection']=='close'
        assert constructions[1]['limits'].max_keepalive_connections==0
        assert not client.client.cookies
        set_diagnostics_level('trace')
        assert client.client.get('https://example.test/synthetic').content==b'synthetic HTML'
        assert requests[-1].headers.get('cookie') is None
        assert len(capture.snapshot()['exchanges'])==2
    finally:
        client.close()
        set_diagnostics_level(previous)
