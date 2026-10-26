"""Anonymous public detail stays anonymous through the real Trace wrapper."""
import json

import httpx
import pytest

import cifereca_numilog_public_client as public
from cifereca.plugin_api import TraceHTTPTransport
from cifereca.http_trace import HTTPTraceCapture
from cifereca.logging_utils import diagnostics_level, set_diagnostics_level


@pytest.mark.parametrize('level', ['normal', 'trace'])
def test_public_detail_trace_preserves_http_and_result(monkeypatch, level):
    capture = HTTPTraceCapture()
    previous = diagnostics_level()
    url = 'https://www.numilog.com/1958476/jefferson-prend-des-risques.ebook'
    content = ('<script type="application/ld+json">' + json.dumps({
        '@type':'Book', 'url':url, 'name':'Fixture'}) + '</script>').encode()
    requests = []

    def serve(request):
        requests.append(request)
        assert 'cookie' not in request.headers and 'authorization' not in request.headers
        return httpx.Response(200, headers={'Content-Type':'text/html'}, stream=httpx.ByteStream(content))

    def transport(**kwargs):
        assert kwargs == {'producer':'provider.numilog.public', 'trust_env':False}
        return TraceHTTPTransport(httpx.MockTransport(serve), capture=capture, **kwargs)

    monkeypatch.setattr(public, 'TraceHTTPTransport', transport)
    set_diagnostics_level(level)
    try:
        assert public.fetch_public_detail(url)
        assert len(requests) == 1
        entries = capture.snapshot()['exchanges']
        assert len(entries) == (1 if level == 'trace' else 0)
        if entries:
            assert entries[0]['response_body']['data'] == content
            assert entries[0]['response_body']['state'] == 'complete'
    finally:
        set_diagnostics_level(previous)
