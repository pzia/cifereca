"""Release discovery goes through Core; raw bytes and one GET are retained."""
import json

import httpx
import github_releases as releases
from cifereca.http_trace import BROWSER_USER_AGENT, HTTPTraceCapture
import cifereca.http_trace as trace_module
from cifereca.logging_utils import diagnostics_level, set_diagnostics_level


def test_release_reads_use_core_capture_and_fresh_cookie_jars(monkeypatch):
    capture=HTTPTraceCapture()
    monkeypatch.setattr(trace_module,'HTTP_TRACE',capture)
    previous=diagnostics_level()
    original=releases.HTTPX_TRANSPORT.client
    constructors,requests=[],[]
    body=json.dumps([{'tag_name':'v1.0','published_at':'2026-10-01T12:00:00Z',
                      'draft':False,'prerelease':False}]).encode()

    def serve(request):
        requests.append(request)
        assert request.headers['user-agent'] == BROWSER_USER_AGENT
        assert request.headers.get('cookie') is None
        assert request.headers.get('authorization') is None
        assert request.headers['accept-encoding']=='identity'
        return httpx.Response(200,headers={'Content-Type':'application/json',
                                         'Set-Cookie':'session=synthetic; Path=/'},
                              stream=httpx.ByteStream(body))

    def construct(**options):
        constructors.append(options.copy())
        return original(**options)

    monkeypatch.setattr(releases.HTTPX_TRANSPORT,'client',construct)
    try:
        for level in ('normal','trace','normal'):
            set_diagnostics_level(level)
            result=releases.fetch_releases(transport=httpx.MockTransport(serve))
            assert result.tag=='v1.0'
        assert len(constructors)==len(requests)==3
        assert all(o['producer']=='dev-updater' and o['follow_redirects'] is False
                   and o['timeout']==10 for o in constructors)
        entries=capture.snapshot()['exchanges']
        assert len(entries)==1 and entries[0]['response_body']['data']==body
        assert entries[0]['response_body']['state']=='complete'
    finally:
        set_diagnostics_level(previous)
