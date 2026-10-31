"""Baen's production client uses Core; its download adapter remains separate."""
import httpx
import cifereca_baen as baen
from cifereca.http_trace import BROWSER_USER_AGENT, HTTPTraceCapture
import cifereca.http_trace as trace_module
from cifereca.logging_utils import diagnostics_level,set_diagnostics_level


def test_baen_core_client_tracks_trace_without_extra_requests(monkeypatch):
    capture=HTTPTraceCapture()
    monkeypatch.setattr(trace_module,'HTTP_TRACE',capture)
    previous=diagnostics_level()
    original=baen.HTTPX_TRANSPORT.client
    constructions,requests=[],[]

    def serve(request):
        requests.append(request)
        assert request.headers['user-agent'] == BROWSER_USER_AGENT
        return httpx.Response(200,stream=httpx.ByteStream(b'synthetic HTML'))

    def construct(**options):
        constructions.append(options.copy())
        return original(**options,transport=httpx.MockTransport(serve))

    monkeypatch.setattr(baen.HTTPX_TRANSPORT,'client',construct)
    set_diagnostics_level('normal')
    session=baen.BaenSession(credential_id='__public__',label='',credentials={},
                             options={},config={},diagnostics=None)
    try:
        for level in ('normal','trace','normal'):
            set_diagnostics_level(level)
            assert session.client.get('https://www.baen.com/synthetic').content==b'synthetic HTML'
        assert len(constructions)==1 and len(requests)==3
        assert constructions[0]['producer']=='provider.baen'
        assert constructions[0]['trust_env'] is False
        assert constructions[0]['follow_redirects'] is False
        entries=capture.snapshot()['exchanges']
        assert len(entries)==1 and entries[0]['response_body']['data']==b'synthetic HTML'
        assert entries[0]['response_body']['state']=='complete'
    finally:
        session.close()
        set_diagnostics_level(previous)
