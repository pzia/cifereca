"""Shared HTTP acquisition keeps its output and uses one observed request."""
from types import SimpleNamespace

import httpx
import pytest

import cifereca_http_transport as plugin
from cifereca.plugin_api import TraceHTTPTransport
from cifereca.http_trace import HTTPTraceCapture
from cifereca.logging_utils import diagnostics_level, set_diagnostics_level


@pytest.mark.parametrize('level', ['normal', 'trace'])
def test_shared_transport_trace_follows_effective_level(tmp_path, monkeypatch, level):
    capture = HTTPTraceCapture()
    previous = diagnostics_level()
    calls = []

    def serve(request):
        calls.append(request)
        return httpx.Response(200, headers={'Content-Type':'application/octet-stream'},
                              stream=httpx.ByteStream(b'synthetic binary'))

    def transport(**kwargs):
        assert kwargs == {'producer':'transport.http', 'trust_env':False}
        return TraceHTTPTransport(httpx.MockTransport(serve), capture=capture, **kwargs)

    monkeypatch.setattr(plugin, 'TraceHTTPTransport', transport)
    set_diagnostics_level(level)
    instance = plugin.HttpTransport(SimpleNamespace(config={}))
    try:
        destination = tmp_path / 'artifact.bin'
        assert instance.fetch({'url':'https://example.test/artifact.bin'},destination) == destination
        assert destination.read_bytes() == b'synthetic binary'
        assert len(calls) == 1
        entries = capture.snapshot()['exchanges']
        assert len(entries) == (1 if level == 'trace' else 0)
        if entries:
            assert entries[0]['response_body']['data'] == b'synthetic binary'
            assert entries[0]['response_body']['state'] == 'complete'
    finally:
        instance.close()
        set_diagnostics_level(previous)
