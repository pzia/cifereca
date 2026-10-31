"""The installed feed path uses the Core public client and its Trace session."""
import httpx
import criticism_feeds as feeds
from cifereca.http_trace import BROWSER_USER_AGENT
from cifereca.http_trace import HTTPTraceCapture
import cifereca.http_trace as trace_module
from cifereca.logging_utils import diagnostics_level,set_diagnostics_level


def test_default_fetch_uses_core_public_client_and_only_trace_captures(monkeypatch):
    capture=HTTPTraceCapture()
    monkeypatch.setattr(trace_module,'HTTP_TRACE',capture)
    previous=diagnostics_level()
    constructions,requests=[],[]
    body=b'<rss version="2.0"><channel/></rss>'

    def serve(request):
        requests.append(request)
        assert request.headers['accept-encoding']=='identity'
        assert request.headers['connection']=='close'
        assert request.headers['user-agent'] == BROWSER_USER_AGENT
        return httpx.Response(200,stream=httpx.ByteStream(body))

    def construct(**options):
        constructions.append(options.copy())
        return feeds.HTTPX_TRANSPORT.client(producer=options['producer'],
            timeout=options['timeout'],trust_env=False,follow_redirects=False,
            transport=httpx.MockTransport(serve))

    monkeypatch.setattr(feeds.HTTPX_TRANSPORT,'public_client',construct)
    try:
        for level in ('normal','trace','normal'):
            set_diagnostics_level(level)
            assert feeds.fetch_public('https://news.example/feed')==('https://news.example/feed',body)
        assert len(constructions)==len(requests)==3
        assert all(row['producer']=='criticism.source.rss' and 0<row['timeout']<=15
                   for row in constructions)
        entries=capture.snapshot()['exchanges']
        assert len(entries)==1 and entries[0]['response_body']['data']==body
        assert entries[0]['response_body']['state']=='complete'
    finally:
        set_diagnostics_level(previous)
