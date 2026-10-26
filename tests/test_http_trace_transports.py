"""HTTPX real transport boundaries; all exchanges are synthetic and offline."""
import asyncio
import gzip
import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import zipfile

import httpx
import pytest

from cifereca.http_trace import HTTPTraceCapture, TraceHTTPTransport, TraceAsyncHTTPTransport
from cifereca.logging_utils import set_diagnostics_level


@pytest.fixture(autouse=True)
def trace():
    from cifereca.logging_utils import diagnostics_level
    previous = diagnostics_level()
    set_diagnostics_level('trace')
    yield
    set_diagnostics_level(previous)


def client(capture, handler):
    return httpx.Client(transport=TraceHTTPTransport(httpx.MockTransport(handler),
                        producer='test', capture=capture), trust_env=False)


def test_off_trace_is_passthrough_and_no_extra_reads():
    capture = HTTPTraceCapture()
    original = httpx.Response(200, stream=httpx.ByteStream(b'abc'))
    set_diagnostics_level('normal')
    with client(capture, lambda r: original) as c:
        with c.stream('GET', 'https://example.test') as r:
            assert not r.is_stream_consumed
            assert r.read() == b'abc'
    assert capture.snapshot()['exchanges'] == []
    with pytest.raises(ValueError):
        capture.export('unused.zip')


def test_stream_unread_partial_complete_and_duplicate_headers(tmp_path):
    capture = HTTPTraceCapture()

    class Stream(httpx.SyncByteStream):
        def __iter__(self):
            yield b'one'
            yield b'two'

    def serve(request):
        return httpx.Response(403, headers=[(b'x-test', b'1'), (b'x-test', b'2')], stream=Stream())

    with client(capture, serve) as c:
        with c.stream('GET', 'https://example.test/?secret=synthetic', headers=[('x-request','a'),('x-request','b')]):
            pass
        with c.stream('GET', 'https://example.test') as r:
            assert next(r.iter_raw()) == b'one'
        assert c.get('https://example.test').content == b'onetwo'
    entries = capture.snapshot()['exchanges']
    assert [e['response_body']['state'] for e in entries] == ['not_read','partial','complete']
    assert entries[0]['response']['headers'] == [['x-test','1'],['x-test','2']]
    assert entries[0]['request']['headers'][-2:] == [['x-request','a'],['x-request','b']]
    path = capture.export(tmp_path / 'http.zip')
    assert os.stat(path).st_mode & 0o777 == 0o600
    with zipfile.ZipFile(path) as archive:
        index = json.loads(archive.read('index.json'))
        for entry in index['exchanges']:
            for side in ['request','response']:
                body = entry[side + '_body']
                data = archive.read(body['blob'])
                assert hashlib.sha256(data).hexdigest() == body['sha256']
                assert len(data) == body['stored_bytes']
    with pytest.raises(FileExistsError):
        capture.export(path)
    assert not list(tmp_path.glob('.cifereca-http-*'))


def test_quotas_concurrency_and_trace_switch():
    capture = HTTPTraceCapture(body_limit=5, session_limit=6000, exchange_limit=8)
    with client(capture, lambda r: httpx.Response(200, stream=httpx.ByteStream(b'0123456789'))) as c:
        with ThreadPoolExecutor(4) as pool:
            assert all(pool.map(lambda _: c.get('https://example.test').content == b'0123456789', range(20)))
    snapshot = capture.snapshot()
    assert len(snapshot['exchanges']) == 8
    assert snapshot['omitted_exchanges'] == 12
    assert snapshot['used_bytes'] <= 6000
    assert all(e['response_body']['state'] == 'quota' for e in snapshot['exchanges'])
    assert all(e['response_body']['data'] == b'01234' for e in snapshot['exchanges'])

    capture = HTTPTraceCapture()
    with client(capture, lambda r: httpx.Response(200, stream=httpx.ByteStream(b'private'))) as c:
        with c.stream('GET', 'https://example.test') as response:
            set_diagnostics_level('normal')
            assert response.read() == b'private'
    body = capture.snapshot()['exchanges'][0]['response_body']
    assert body['state'] == 'trace_disabled'
    assert body['data'] == b''


def test_timeout_and_stream_failure_do_not_change_business_errors():
    capture = HTTPTraceCapture()
    error = httpx.ReadTimeout('synthetic timeout')

    def fail(request):
        raise error

    with client(capture, fail) as c:
        with pytest.raises(httpx.ReadTimeout) as caught:
            c.get('https://example.test')
    assert caught.value is error
    assert capture.snapshot()['exchanges'][0]['outcome'] == 'transport_error'

    class Broken(httpx.SyncByteStream):
        def __iter__(self):
            yield b'prefix'
            raise error

    with client(capture, lambda r: httpx.Response(200, stream=Broken())) as c:
        with pytest.raises(httpx.ReadTimeout):
            c.get('https://example.test')
    body = capture.snapshot()['exchanges'][-1]['response_body']
    assert body['state'] == 'error' and body['data'] == b'prefix'


def test_real_http_request_redirect_cookies_compression_and_send():
    capture = HTTPTraceCapture()
    compressed = gzip.compress(b'plain response')
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = self.rfile.read(int(self.headers['Content-Length']))
            calls.append((self.path, body))
            self.send_response(302)
            self.send_header('Location','/final')
            self.send_header('Set-Cookie','session=synthetic; Path=/')
            self.send_header('Content-Length','0')
            self.end_headers()

        def do_GET(self):
            calls.append((self.path, self.headers.get('Cookie')))
            self.send_response(200)
            self.send_header('Content-Encoding','gzip')
            self.send_header('X-Duplicate','a')
            self.send_header('X-Duplicate','b')
            self.send_header('Content-Length',str(len(compressed)))
            self.end_headers()
            self.wfile.write(compressed)

    server = ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with httpx.Client(transport=TraceHTTPTransport(capture=capture, trust_env=False),
                          trust_env=False, follow_redirects=True) as c:
            request = c.build_request('POST', f'http://127.0.0.1:{server.server_port}/start', content=b'payload')
            response = c.send(request)
            assert response.content == b'plain response'
            assert len(response.history) == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    assert calls == [('/start',b'payload'),('/final','session=synthetic')]
    entries = capture.snapshot()['exchanges']
    assert len(entries) == 2
    assert entries[0]['request_body']['data'] == b'payload'
    assert entries[0]['request_body']['state'] == 'complete'
    assert entries[1]['response_body']['data'] == compressed
    assert entries[1]['response_body']['representation'] == 'transport_bytes'
    assert entries[1]['response']['headers'][-3:-1] == [['X-Duplicate','a'],['X-Duplicate','b']]


def test_async_stream_request_cancellation_and_close():
    capture = HTTPTraceCapture()

    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'one'
            yield b'two'

    class Transport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            assert await request.aread() == b'payload'
            return httpx.Response(200, stream=Stream())

    # Use streamed content so the transport genuinely consumes request bytes.
    class Payload(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'payload'

    async def run():
        async with httpx.AsyncClient(transport=TraceAsyncHTTPTransport(Transport(), capture=capture)) as c:
            async with c.stream('POST','https://example.test', content=Payload()) as r:
                assert await r.aread() == b'onetwo'

    asyncio.run(run())
    entry = capture.snapshot()['exchanges'][0]
    assert entry['request_body']['data'] == b'payload'
    assert entry['response_body']['data'] == b'onetwo'
    assert entry['outcome'] == 'complete'

    class Cancelled(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            raise asyncio.CancelledError()

    async def cancel():
        async with httpx.AsyncClient(transport=TraceAsyncHTTPTransport(Cancelled(), capture=capture)) as c:
            with pytest.raises(asyncio.CancelledError):
                await c.get('https://example.test')

    asyncio.run(cancel())
    assert capture.snapshot()['exchanges'][-1]['error_type'] == 'CancelledError'


def test_new_process_session_is_empty_and_export_survives(tmp_path):
    capture = HTTPTraceCapture()
    with client(capture, lambda r: httpx.Response(500, content=b'error')) as c:
        c.get('https://example.test')
    destination = capture.export(tmp_path / 'durable.zip')
    replacement = HTTPTraceCapture()
    assert replacement.session_ref != capture.session_ref
    assert not replacement.snapshot()['exchanges']
    assert destination.is_file()


def test_export_failure_and_symlink_preserve_destination(tmp_path, monkeypatch):
    capture = HTTPTraceCapture()
    target = tmp_path / 'target'
    target.write_bytes(b'keep')
    link = tmp_path / 'export.zip'
    link.symlink_to(target)
    with pytest.raises(FileExistsError):
        capture.export(link)
    assert target.read_bytes() == b'keep'
    monkeypatch.setattr(os, 'fsync', lambda *_: (_ for _ in ()).throw(OSError('disk full')))
    with pytest.raises(OSError):
        capture.export(tmp_path / 'failure.zip')
    assert not (tmp_path / 'failure.zip').exists()
    assert not list(tmp_path.glob('.cifereca-http-*'))


def test_total_budget_metadata_omissions_and_capture_failure_are_nonfatal(monkeypatch):
    capture = HTTPTraceCapture(body_limit=10000, session_limit=800, metadata_limit=400)
    with client(capture, lambda r: httpx.Response(200, stream=httpx.ByteStream(b'x' * 5000))) as c:
        assert c.get('https://example.test').content == b'x' * 5000
        assert c.get('https://example.test/' + 'x' * 1000).status_code == 200
    snapshot = capture.snapshot()
    assert snapshot['used_bytes'] <= 800
    assert snapshot['omitted_exchanges'] == 1
    body = snapshot['exchanges'][0]['response_body']
    assert body['state'] == 'quota' and body['observed_bytes'] == 5000
    assert len(body['data']) < 800
    monkeypatch.setattr(capture, '_body', lambda: (_ for _ in ()).throw(OSError('synthetic')))
    with client(capture, lambda r: httpx.Response(200, content=b'unchanged')) as c:
        assert c.get('https://example.test').content == b'unchanged'


def test_only_existing_task_scope_is_correlated():
    from cifereca.diagnostic_task_context import diagnostic_task_scope, diagnostic_task_reference
    capture = HTTPTraceCapture()
    with client(capture, lambda r: httpx.Response(200, content=b'yes')) as c:
        c.get('https://example.test')
        with diagnostic_task_scope():
            ref = diagnostic_task_reference()
            c.get('https://example.test')
    first, second = capture.snapshot()['exchanges']
    assert 'task_ref' not in first and 'operation_ref' not in first
    assert second['task_ref'] == ref
    assert 'operation_ref' not in second


def test_async_unread_partial_and_raw_compression():
    capture = HTTPTraceCapture()
    compressed = gzip.compress(b'decoded')

    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield compressed[:5]
            yield compressed[5:]

    class Transport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            return httpx.Response(200, headers={'Content-Encoding':'gzip'}, stream=Stream())

    async def run():
        async with httpx.AsyncClient(transport=TraceAsyncHTTPTransport(Transport(), capture=capture)) as c:
            async with c.stream('GET','https://example.test'):
                pass
            async with c.stream('GET','https://example.test') as r:
                iterator = r.aiter_raw()
                assert await anext(iterator) == compressed[:5]
                await iterator.aclose()
            assert (await c.get('https://example.test')).content == b'decoded'
    asyncio.run(run())
    entries = capture.snapshot()['exchanges']
    assert [e['response_body']['state'] for e in entries] == ['not_read','partial','complete']
    assert entries[-1]['response_body']['data'] == compressed


def test_public_broker_export_has_same_trace_gate(tmp_path, monkeypatch):
    from cifereca.brokers import DiagnosticsBrokerImpl
    import cifereca.http_trace as module
    capture = HTTPTraceCapture()
    monkeypatch.setattr(module, 'HTTP_TRACE', capture)
    broker = DiagnosticsBrokerImpl(service=None)
    assert broker.http_trace_status()['active'] is True
    assert broker.export_http_trace(str(tmp_path / 'export.zip')) == str(tmp_path / 'export.zip')
    set_diagnostics_level('normal')
    assert broker.http_trace_status()['active'] is False
    with pytest.raises(ValueError):
        broker.export_http_trace(str(tmp_path / 'blocked.zip'))
    assert not (tmp_path / 'blocked.zip').exists()
