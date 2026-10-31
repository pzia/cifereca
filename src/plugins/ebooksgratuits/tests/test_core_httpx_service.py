"""The provider constructs HTTP through Core, including live Trace switches."""
import httpx
import cifereca_ebooksgratuits as provider
from cifereca.http_trace import BROWSER_USER_AGENT, HTTPTraceCapture
import cifereca.http_trace as trace_module
from cifereca.logging_utils import diagnostics_level, set_diagnostics_level


def test_core_service_tracks_trace_on_existing_client(monkeypatch):
    capture=HTTPTraceCapture()
    monkeypatch.setattr(trace_module,'HTTP_TRACE',capture)
    previous=diagnostics_level()
    original=provider.HTTPX_TRANSPORT.client
    constructions,requests=[],[]

    def serve(request):
        requests.append(request)
        assert request.headers['user-agent'] == BROWSER_USER_AGENT
        return httpx.Response(200,stream=httpx.ByteStream(b'synthetic bytes'))

    def construct(**options):
        constructions.append(options.copy())
        return original(**options,transport=httpx.MockTransport(serve))

    monkeypatch.setattr(provider.HTTPX_TRANSPORT,'client',construct)
    set_diagnostics_level('normal')
    session=provider.EbooksSession(credential_id='__public__',label='',credentials={},
                           options={},config={},diagnostics=None)
    try:
        for level in ('normal','trace','normal'):
            set_diagnostics_level(level)
            assert session.client.get('https://example.test/synthetic').content==b'synthetic bytes'
        assert len(constructions)==1 and len(requests)==3
        assert constructions[0]['producer']=='provider.ebooksgratuits'
        entries=capture.snapshot()['exchanges']
        assert len(entries)==1
        assert entries[0]['response_body']['data']==b'synthetic bytes'
        assert entries[0]['response_body']['state']=='complete'
        session.close()
        session.connect()
        assert len(constructions)==2 and len(requests)==3
        set_diagnostics_level('trace')
        assert session.client.get('https://example.test/reconnected').content==b'synthetic bytes'
        assert len(capture.snapshot()['exchanges'])==2
        assert len(requests)==4
    finally:
        session.close()
        set_diagnostics_level(previous)
