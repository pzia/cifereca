"""Shared acquisition uses the real Core service with no extra request."""
from types import SimpleNamespace

import httpx
import pytest

import cifereca_http_transport as plugin
from cifereca.http_trace import BROWSER_USER_AGENT, HTTPTraceCapture
import cifereca.http_trace as trace_module
from cifereca.logging_utils import diagnostics_level, set_diagnostics_level


@pytest.mark.parametrize('level', ['normal', 'trace'])
def test_shared_acquisition_uses_core_service(tmp_path, monkeypatch, level):
    capture = HTTPTraceCapture()
    monkeypatch.setattr(trace_module, 'HTTP_TRACE', capture)
    previous = diagnostics_level()
    calls, constructions = [], []
    original = plugin.HTTPX_TRANSPORT.client

    def serve(request):
        calls.append(request)
        assert request.headers['user-agent'] == BROWSER_USER_AGENT
        return httpx.Response(200, headers={'Content-Type':'application/octet-stream'},
                              stream=httpx.ByteStream(b'synthetic binary'))

    def construct(**kwargs):
        constructions.append(kwargs.copy())
        return original(**kwargs, transport=httpx.MockTransport(serve))

    monkeypatch.setattr(plugin.HTTPX_TRANSPORT, 'client', construct)
    set_diagnostics_level(level)
    instance = plugin.HttpTransport(SimpleNamespace(config={}))
    try:
        destination = tmp_path / 'artifact.bin'
        assert instance.fetch({'url':'https://example.test/artifact.bin'}, destination) == destination
        assert destination.read_bytes() == b'synthetic binary'
        assert len(calls) == len(constructions) == 1
        assert constructions[0]['producer'] == 'transport.http'
        assert constructions[0]['follow_redirects'] is False
        assert constructions[0]['trust_env'] is False
        entries = capture.snapshot()['exchanges']
        assert len(entries) == (1 if level == 'trace' else 0)
        if entries:
            assert entries[0]['response_body']['data'] == b'synthetic binary'
            assert entries[0]['response_body']['state'] == 'complete'
    finally:
        instance.close()
        set_diagnostics_level(previous)


def test_existing_client_follows_trace_switch_without_reconstruction(tmp_path, monkeypatch):
    capture = HTTPTraceCapture()
    monkeypatch.setattr(trace_module, 'HTTP_TRACE', capture)
    previous = diagnostics_level()
    original = plugin.HTTPX_TRANSPORT.client
    constructions, requests = [], []

    def serve(request):
        requests.append(request)
        assert request.headers['user-agent'] == BROWSER_USER_AGENT
        return httpx.Response(200, stream=httpx.ByteStream(b'bytes'))

    def construct(**kwargs):
        constructions.append(kwargs.copy())
        return original(**kwargs, transport=httpx.MockTransport(serve))

    monkeypatch.setattr(plugin.HTTPX_TRANSPORT, 'client', construct)
    set_diagnostics_level('normal')
    instance = plugin.HttpTransport(SimpleNamespace(config={}))
    try:
        for index, level in enumerate(('normal', 'trace', 'normal')):
            set_diagnostics_level(level)
            instance.fetch({'url':'https://example.test/artifact'}, tmp_path / f'{index}.bin')
        assert len(constructions) == 1 and len(requests) == 3
        assert len(capture.snapshot()['exchanges']) == 1
    finally:
        instance.close()
        set_diagnostics_level(previous)
