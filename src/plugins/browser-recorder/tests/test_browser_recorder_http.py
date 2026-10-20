"""P1 #2528: request/response chronology, byte guards and RAM-only captures.

Entire fake corpus; no real provider request, cookie, license or filesystem
export. A later review step must authorize any private value leaving RAM.
"""
from __future__ import annotations

from threading import Event

import pytest

from browser_recorder import RecorderError
from browser_recorder_http import (
    MAX_BODY_BYTES, PrivateHttpTrace, record_http,
)

ORIGIN = "https://fake-books.example.org"
FOREIGN = "https://identity.example.org"


class Request:
    def __init__(self, url, *, method="GET", headers=None, data=None, tab=0):
        self.url = url
        self.method = method
        self.headers = headers or {}
        self._data = data
        self.read_post_data = 0

    @property
    def post_data_buffer(self):
        self.read_post_data += 1
        return self._data

    def sizes(self):
        return {
            "requestBodySize": len(self._data or b""),
            "responseBodySize": getattr(self, "_response_size", 0),
        }


class Response:
    def __init__(self, request, *, status=200, mime="application/json",
                 data=b'{"ok":true}', content_length=True, headers=None):
        self.request = request
        self.status = status
        self._data = data
        self.body_reads = 0
        self.headers = {"content-type": mime}
        if content_length:
            self.headers["content-length"] = str(len(data))
        self.headers.update(headers or {})
        request._response_size = len(data)

    def body(self):
        self.body_reads += 1
        return self._data


def emit(trace, request, response):
    trace.request(request)
    trace.response(response)
    trace.finished(request)


def test_correlated_user_navigation_post_json_redirect_and_get():
    trace = PrivateHttpTrace((ORIGIN,))
    post = Request(
        ORIGIN + "/login?book_id=214", method="POST",
        headers={"Content-Type": "application/json",
                 "Content-Length": "40",
                 "Authorization": "Bearer NEVER_LOG",
                 "Cookie": "sid=NEVER_LOG"},
        data=b'{"user_id":"private-7","book_id":"214"}',
    )
    post.headers["Content-Length"] = str(len(post._data))
    redirect = Response(
        post, status=302, data=b"",
        headers={"location": "/library?book_id=214", "Set-Cookie": "SECRET"},
    )
    emit(trace, post, redirect)
    get = Request(ORIGIN + "/library?book_id=214", method="GET")
    response = Response(get, mime="application/json", data=b'{"book_id":"214"}')
    emit(trace, get, response)

    assert len(trace.exchanges) == 2
    first, second = trace.exchanges
    assert [e.sequence for e in trace.exchanges] == [1, 2]
    assert first.method == "POST" and first.status == 302
    assert first.request_body_state == "captured"
    assert first.request_body == '{"user_id":"private-7","book_id":"214"}'
    assert first.response_body_state == "redirect"
    assert first.redirect_to == "/library?book_id=214"
    assert second.method == "GET" and second.status == 200
    assert second.response_body == '{"book_id":"214"}'
    assert redirect.body_reads == 0 and response.body_reads == 1
    assert "NEVER_LOG" not in repr(trace)
    assert "private-7" not in repr(first)
    assert "private-7" not in repr(trace)
    assert "authorization" not in first.request_headers
    assert "cookie" not in first.request_headers
    assert "set-cookie" not in first.response_headers
    trace.destroy()
    assert trace.exchanges == []


def test_content_length_and_actual_byte_count_both_required_before_reading():
    trace = PrivateHttpTrace((ORIGIN,), max_body=64)
    body = b"x" * 100
    oversized = Response(Request(ORIGIN + "/huge"), data=body,
                         headers={"content-length": "1"})
    emit(trace, oversized.request, oversized)
    assert oversized.body_reads == 0
    assert trace.exchanges[0].response_body_state == "body_omitted"

    unknown = Response(Request(ORIGIN + "/stream"),
                       content_length=False, data=b"small")
    emit(trace, unknown.request, unknown)
    assert unknown.body_reads == 0
    assert trace.exchanges[1].response_body_state == "body_omitted"

    compressed = Response(Request(ORIGIN + "/bomb"), data=b"abc",
                          headers={"content-encoding": "gzip"})
    emit(trace, compressed.request, compressed)
    assert compressed.body_reads == 0
    assert trace.exchanges[2].response_body_state == "body_omitted"

    actual_missing = Request(ORIGIN + "/unknown")
    actual_missing.sizes = lambda: {}
    response = Response(actual_missing, data=b"hello")
    emit(trace, actual_missing, response)
    assert response.body_reads == 0


@pytest.mark.parametrize("mime", [
    "image/png", "font/woff2", "application/pdf", "application/epub+zip",
    "application/octet-stream", "video/mp4",
])
def test_binary_resources_never_read(mime):
    trace = PrivateHttpTrace((ORIGIN,))
    req = Request(ORIGIN + "/resource")
    response = Response(req, mime=mime, data=b"\0PRIVATE_PAYLOAD\0")
    emit(trace, req, response)
    assert response.body_reads == 0
    assert trace.exchanges[0].response_body_state == "binary_skipped"
    assert trace.omitted["binary_skipped"] == 1


def test_request_body_requires_declared_and_effective_bounded_size():
    trace = PrivateHttpTrace((ORIGIN,), max_body=64)
    huge = Request(
        ORIGIN + "/post", method="POST",
        headers={"content-type": "application/json", "content-length": "4"},
        data=b"z" * 200,
    )
    emit(trace, huge, Response(huge, data=b"{}"))
    assert huge.read_post_data == 0
    assert trace.exchanges[0].request_body is None
    without_length = Request(
        ORIGIN + "/post2", method="POST",
        headers={"content-type": "application/json"},
        data=b'{"id":1}',
    )
    emit(trace, without_length, Response(without_length))
    assert without_length.read_post_data == 0
    assert trace.exchanges[1].request_body_state == "body_omitted"


def test_total_budget_and_origin_scope_are_enforced():
    trace = PrivateHttpTrace((ORIGIN,), limit=1, total_body=4)
    outside = Request(FOREIGN + "/secret", headers={"Authorization": "Bearer secret"})
    trace.request(outside)
    assert trace.exchanges == [] and trace.omitted["out_of_scope"] == 1
    response = Response(Request(ORIGIN + "/small"), data=b"hello")
    emit(trace, response.request, response)
    assert response.body_reads == 0
    assert trace.exchanges[0].response_body_state == "body_omitted"
    trace.request(Request(ORIGIN + "/another"))
    assert len(trace.exchanges) == 1 and trace.omitted["limit"] == 1


def test_network_error_is_correlated_without_exception_details():
    trace = PrivateHttpTrace((ORIGIN,))
    req = Request(ORIGIN + "/private?token=TOP_SECRET")
    trace.request(req)
    trace.failed(req)
    assert trace.exchanges[0].network_error
    assert trace.exchanges[0].response_body_state == "network_error"
    assert "TOP_SECRET" not in repr(trace)
    assert not trace._pending


class FakePage:
    def __init__(self, context):
        self.context = context

    def goto(self, url, **kw):
        req = Request(url)
        for listener in self.context.events.get("request", []):
            listener(req)
        resp = Response(req, data=b'{"fake":true}')
        for listener in self.context.events.get("response", []):
            listener(resp)
        for listener in self.context.events.get("requestfinished", []):
            listener(req)
        self.context.stopped.set()

    def wait_for_timeout(self, timeout):
        pass


class FakeContext:
    def __init__(self, stopped):
        self.stopped = stopped
        self.events = {}
        self.closed = False

    def on(self, kind, callback):
        self.events.setdefault(kind, []).append(callback)

    def new_page(self):
        return FakePage(self)

    def close(self):
        self.closed = True


class FakePlaywright:
    def __init__(self, ctx):
        self.chromium = self
        self.context = ctx

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        pass

    def launch(self, **kw):
        assert kw["headless"] is False
        return self

    def new_context(self, **kw):
        assert kw["accept_downloads"] is False
        assert kw["service_workers"] == "block"
        return self.context

    def close(self):
        pass


def test_record_http_returns_only_in_memory_after_operator_stop(tmp_path):
    stopped = Event()
    ctx = FakeContext(stopped)
    trace = record_http(ORIGIN + "/catalog", stopped, factory=lambda: FakePlaywright(ctx))
    try:
        assert len(trace.exchanges) == 1
        assert trace.exchanges[0].response_body == '{"fake":true}'
        assert ctx.closed
        assert list(tmp_path.iterdir()) == []  # NO raw HAR/ZIP/secret file
    finally:
        trace.destroy()


def test_origin_mismatch_is_rejected_before_browser_opening():
    stop = Event()
    with pytest.raises(RecorderError, match="invalid_origin"):
        record_http(ORIGIN + "/catalog", stop, origins=(FOREIGN,),
                    factory=lambda: pytest.fail("Browser must not start"))
