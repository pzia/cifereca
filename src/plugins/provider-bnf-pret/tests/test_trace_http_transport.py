"""Provider consumption through its real explicit Core transport, offline."""
import httpx
import pytest

from cifereca_bnf_pret_http import ReaderHTTPClient, ACTIVITY
from cifereca.http_trace import HTTPTraceCapture
from cifereca.logging_utils import diagnostics_level, set_diagnostics_level
import cifereca.http_trace as trace_module


@pytest.mark.parametrize('level', ['normal', 'trace'])
def test_bnf_activity_capture_follows_effective_trace_only(monkeypatch, level):
    previous = diagnostics_level()
    capture = HTTPTraceCapture()
    monkeypatch.setattr(trace_module, 'HTTP_TRACE', capture)
    calls = []
    content = b'<html><body>synthetic activity</body></html>'

    def serve(request):
        calls.append(str(request.url))
        return httpx.Response(200, headers={'Content-Type':'text/html'}, stream=httpx.ByteStream(content))

    set_diagnostics_level(level)
    reader = ReaderHTTPClient(transport=httpx.MockTransport(serve))
    try:
        assert reader.request('GET', ACTIVITY) == (ACTIVITY, content.decode())
        assert calls == [ACTIVITY]
        entries = capture.snapshot()['exchanges']
        assert len(entries) == (1 if level == 'trace' else 0)
        if entries:
            assert entries[0]['request']['producer'] == 'provider.bnf-pret'
            assert entries[0]['response_body']['data'] == content
            assert entries[0]['response_body']['state'] == 'complete'
    finally:
        reader.close()
        set_diagnostics_level(previous)


def test_bnf_constructs_only_through_core_service_and_client_tracks_switches(monkeypatch):
    from cifereca.plugin_api import HTTPX_TRANSPORT
    import cifereca_bnf_pret_http as provider
    previous=diagnostics_level()
    capture=HTTPTraceCapture()
    monkeypatch.setattr(trace_module,'HTTP_TRACE',capture)
    original=HTTPX_TRANSPORT.client
    constructions=[]
    requests=[]

    def construct(**options):
        constructions.append(options.copy())
        return original(**options)

    def serve(request):
        requests.append(request)
        return httpx.Response(200,headers={'Content-Type':'text/html'},
                              stream=httpx.ByteStream(b'<html>activity</html>'))

    monkeypatch.setattr(HTTPX_TRANSPORT,'client',construct)
    set_diagnostics_level('normal')
    reader=provider.ReaderHTTPClient(transport=httpx.MockTransport(serve))
    try:
        assert len(constructions)==1
        assert constructions[0]['producer']=='provider.bnf-pret'
        assert constructions[0]['trust_env'] is False
        assert constructions[0]['follow_redirects'] is False
        reader.request('GET',ACTIVITY)
        assert not capture.snapshot()['exchanges']
        set_diagnostics_level('trace')
        reader.request('GET',ACTIVITY)
        assert len(capture.snapshot()['exchanges'])==1
        set_diagnostics_level('normal')
        reader.request('GET',ACTIVITY)
        assert len(capture.snapshot()['exchanges'])==1
        assert len(requests)==3 and len(constructions)==1
    finally:
        reader.close()
        set_diagnostics_level(previous)
