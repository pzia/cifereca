"""P1 #2528: request/response chronology, byte guards and RAM-only captures.

Entire fake corpus; no real provider request, cookie, license or filesystem
export. A later review step must authorize any private value leaving RAM.
"""
from __future__ import annotations

from threading import Event

import pytest

from browser_recorder import RecorderError
from browser_recorder_http import (
    DEFAULT_BODY_BYTES, MAX_BODY_BYTES, PrivateHttpTrace, record_http,
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
    assert unknown.body_reads == 1
    assert trace.exchanges[1].response_body_state == "captured"
    assert trace.exchanges[1].response_body == "small"

    compressed = Response(Request(ORIGIN + "/bomb"), data=b"abc",
                          headers={"content-encoding": "gzip"})
    emit(trace, compressed.request, compressed)
    assert compressed.body_reads == 0
    assert trace.exchanges[2].response_body_state == "body_omitted"
    assert trace.omitted["compressed"] == 1

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
    assert without_length.read_post_data == 1
    assert trace.exchanges[1].request_body_state == "captured"
    assert trace.exchanges[1].request_body == '{"id":1}'


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
        self.handlers = {}
        self.closed = False

    def on(self, name, callback):
        self.handlers.setdefault(name, []).append(callback)

    def is_closed(self):
        return self.closed

    def close(self):
        self.closed = True
        for callback in self.handlers.get("close", []):
            callback(self)

    def crash(self):
        for callback in self.handlers.get("crash", []):
            callback(self)
        self.close()

    def goto(self, url, **kw):
        req = Request(url)
        for listener in self.context.events.get("request", []):
            listener(req)
        resp = Response(req, data=b'{"fake":true}')
        for listener in self.context.events.get("response", []):
            listener(resp)
        for listener in self.context.events.get("requestfinished", []):
            listener(req)
        if self.context.stop_on_navigation:
            self.context.stopped.set()
        if self.context.close_during_navigation:
            self.close()
            raise RuntimeError("Synthetic TargetClosed")

    def wait_for_timeout(self, timeout):
        if self.context.on_wait:
            self.context.on_wait(self)
        if self.closed:
            raise RuntimeError("Synthetic TargetClosed")


class FakeContext:
    def __init__(self, stopped):
        self.stopped = stopped
        self.events = {}
        self.closed = False
        self.pages = []
        self.stop_on_navigation = True
        self.close_during_navigation = False
        self.on_wait = None

    def on(self, kind, callback):
        self.events.setdefault(kind, []).append(callback)

    def new_page(self):
        page = FakePage(self)
        self.pages.append(page)
        for callback in self.events.get("page", []):
            callback(page)
        return page

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
        self.context_options = kw
        assert kw["accept_downloads"] is True
        assert kw["service_workers"] == "block"
        return self.context

    def close(self):
        pass

    def is_connected(self):
        return self.context.connected if hasattr(self.context, "connected") else True


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


@pytest.mark.parametrize("enabled", (False, True))
def test_identity_negotiation_is_explicit_and_does_not_relax_body_guards(enabled):
    stopped = Event()
    ctx = FakeContext(stopped)
    runtime = FakePlaywright(ctx)
    trace = record_http(ORIGIN + "/catalog", stopped,
                        prefer_identity=enabled, factory=lambda: runtime)
    try:
        assert trace.content_negotiation == ("identity_requested" if enabled else "browser_default")
        assert runtime.context_options.get("extra_http_headers") == (
            {"Accept-Encoding": "identity"} if enabled else None
        )
        # A server can ignore Accept-Encoding: identity. Never decompress it.
        response = Response(Request(ORIGIN + "/forced"), data=b"compressed",
                            headers={"content-encoding": "gzip"})
        emit(trace, response.request, response)
        assert response.body_reads == 0
        assert trace.exchanges[-1].response_body_reason == "compressed"
    finally:
        trace.destroy()


def test_invalid_strategy_rejected_before_browser_launch():
    with pytest.raises(RecorderError, match="invalid_capture_strategy"):
        record_http(ORIGIN, Event(), prefer_identity="true",
                    factory=lambda: pytest.fail("no browser"))


def test_pause_resume_and_multiple_tabs_keep_private_correlations():
    trace = PrivateHttpTrace((ORIGIN,))
    page1, page2 = object(), object()
    first = Request(ORIGIN + "/catalog")
    first.frame = type("Frame", (), {"page": page1})()
    second = Request(ORIGIN + "/search")
    second.frame = type("Frame", (), {"page": page2})()
    ignored = Request(ORIGIN + "/private?password=NEVER_LOG")
    trace.request(first)
    trace.pause()
    trace.request(ignored)
    trace.resume()
    trace.request(second)
    emit_response = Response(first, data=b'{"ok":1}')
    trace.response(emit_response)
    trace.finished(first)
    emit_response2 = Response(second, data=b'{"ok":2}')
    trace.response(emit_response2)
    trace.finished(second)
    assert len(trace.exchanges) == 2
    assert [x.page_id for x in trace.exchanges] == [1, 2]
    assert all(isinstance(x.duration_ms, int) and x.duration_ms >= 0
               for x in trace.exchanges)
    assert "NEVER_LOG" not in repr(trace)
    trace.destroy()



def test_paused_inflight_body_is_never_read_after_resume():
    # A fast pause/resume between Playwright events must still prevent a
    # pre-pause authenticated POST/response from leaking into review.
    from browser_recorder_http import CapturePause

    control = CapturePause()
    trace = PrivateHttpTrace((ORIGIN,), pause_control=control)
    first_data = b'{"book":214}'
    first = Request(ORIGIN + "/before?book_id=214", method="POST",
                    headers={"content-type": "application/json",
                             "content-length": str(len(first_data))},
                    data=first_data)
    trace.request(first)
    response = Response(first, data=b'{"secret":"PRIVATE"}')
    control.set_paused(True)
    control.set_paused(False)
    trace.response(response)
    trace.finished(first)
    assert response.body_reads == 0
    assert first.read_post_data == 0
    assert len(trace.exchanges) == 1
    assert trace.exchanges[0].response_body_state == "body_omitted"
    assert trace.exchanges[0].request_body_state == "body_omitted"
    assert trace.omitted["paused"] == 1
    assert not trace._pending and not trace._responses

    control.set_paused(True)
    trace.request(Request(ORIGIN + "/ignored?token=PRIVATE"))
    assert len(trace.exchanges) == 1
    control.set_paused(False)
    second = Request(ORIGIN + "/after")
    second_response = Response(second, data=b'{"ok":true}')
    emit(trace, second, second_response)
    assert len(trace.exchanges) == 2
    assert trace.exchanges[1].response_body == '{"ok":true}'
    assert second_response.body_reads == 1
    assert trace.omitted["paused"] >= 2
    trace.destroy()


def test_direct_pause_discards_pending_body_and_redirect_payload():
    trace = PrivateHttpTrace((ORIGIN,))
    req = Request(ORIGIN + "/pending")
    trace.request(req)
    response = Response(req, data=b'{"private":"SECRET"}')
    trace.pause()
    trace.response(response)
    trace.finished(req)
    assert response.body_reads == 0
    assert trace.exchanges[0].response_body is None
    assert trace.exchanges[0].response_body_state == "body_omitted"
    trace.resume()
    assert not trace._pending and not trace._responses
    trace.destroy()


def test_explicit_third_party_metadata_never_reads_headers_or_bodies():
    """A separately consented identity origin exposes only method/status.

    Property traps enforce a stronger invariant than merely asserting that
    a private token is absent from the final ZIP.
    """
    from browser_recorder_review import IdentifierReview
    from browser_recorder_zip import prepare_reviewed_zip

    class ThirdParty(Request):
        @property
        def headers(self):
            raise AssertionError("third-party request headers must stay unread")

        @headers.setter
        def headers(self, _value):
            pass

        @property
        def post_data_buffer(self):
            raise AssertionError("third-party request body must stay unread")

        def sizes(self):
            raise AssertionError("third-party body sizes must stay unread")

    class ThirdPartyResponse:
        status = 302
        def __init__(self, request):
            self.request = request

        @property
        def headers(self):
            raise AssertionError("third-party headers/redirect must stay unread")

        def body(self):
            raise AssertionError("third-party response must stay unread")

    trace = PrivateHttpTrace((ORIGIN,), metadata_only_origins=(FOREIGN,))
    try:
        target = ThirdParty(
            FOREIGN + "/login?state=FICTIONAL_PRIVATE_OAUTH&code=SECRET",
            method="POST",
        )
        response = ThirdPartyResponse(target)
        emit(trace, target, response)
        assert len(trace.exchanges) == 1
        entry = trace.exchanges[0]
        assert entry.metadata_only is True
        assert entry.method == "POST" and entry.status == 302
        assert entry.url == "" and entry.redirect_to is None
        assert not entry.request_headers and not entry.response_headers
        assert entry.mime == "" and entry.request_body is None
        assert entry.response_body is None and entry.duration_ms >= 0
        assert not trace._pending and not trace._responses
        review = IdentifierReview(trace)
        try:
            assert not review.candidates()
            summary = prepare_reviewed_zip(review).preview()
            import json
            published = json.dumps(summary)
            assert "FICTIONAL_PRIVATE_OAUTH" not in published
            assert FOREIGN not in published
            exchange = next(x for x in summary["members"]
                            if x["name"] == "exchanges/0001.json")
            document = json.loads(exchange["text"])
            assert document["metadata_only"] is True
            assert document["url"] == "" and document["status"] == 302
            assert not document["bodies"]["request"]["file"]
            assert not document["bodies"]["response"]["file"]
        finally:
            review.destroy()
    finally:
        trace.destroy()


def test_unlisted_third_party_remains_invisible_and_metadata_scope_is_bounded():
    trace = PrivateHttpTrace((ORIGIN,))
    emit(trace, Request(FOREIGN + "/login?token=PRIVATE"),
         Response(Request(FOREIGN + "/login?token=PRIVATE")))
    assert trace.exchanges == []
    assert trace.omitted["out_of_scope"] == 1
    with pytest.raises(RecorderError, match="invalid_origin"):
        PrivateHttpTrace((ORIGIN,), metadata_only_origins=(
            FOREIGN, "https://id2.example.org", "https://id3.example.org",
            "https://id4.example.org", "https://id5.example.org",
        ))
    with pytest.raises(RecorderError, match="invalid_origin"):
        PrivateHttpTrace((ORIGIN,), metadata_only_origins=("http://localhost",))
    trace.destroy()



def test_close_last_chromium_window_returns_sealed_ram_trace_without_stop(tmp_path):
    stopped = Event()
    ctx = FakeContext(stopped)
    ctx.stop_on_navigation = False
    ctx.on_wait = lambda page: page.close()
    trace = record_http(ORIGIN + "/catalog", stopped,
                        factory=lambda: FakePlaywright(ctx), max_seconds=2)
    try:
        assert not stopped.is_set()
        assert trace.end_reason == "browser_closed"
        assert len(trace.exchanges) == 1
        assert trace.exchanges[0].response_body == '{"fake":true}'
        assert ctx.pages[0].is_closed()  # browser window was closed
        assert list(tmp_path.iterdir()) == []
    finally:
        trace.destroy()


def test_first_tab_close_keeps_session_until_last_tab_closes():
    stopped = Event()
    ctx = FakeContext(stopped)
    ctx.stop_on_navigation = False
    actions = []

    def on_wait(page):
        actions.append(page)
        if len(actions) == 1:
            ctx.new_page()
            page.close()
        else:
            page.close()

    ctx.on_wait = on_wait
    trace = record_http(ORIGIN + "/catalog", stopped,
                        factory=lambda: FakePlaywright(ctx), max_seconds=2)
    assert trace.end_reason == "browser_closed"
    assert len(actions) == 2 and actions[0] is not actions[1]
    assert len(trace.exchanges) == 1
    trace.destroy()


def test_closing_window_during_navigation_is_not_a_leaked_error():
    stopped = Event()
    ctx = FakeContext(stopped)
    ctx.stop_on_navigation = False
    ctx.close_during_navigation = True
    trace = record_http(ORIGIN + "/catalog", stopped,
                        factory=lambda: FakePlaywright(ctx), max_seconds=2)
    assert trace.end_reason == "browser_closed"
    assert len(trace.exchanges) == 1
    trace.destroy()


def test_stop_and_last_window_close_race_finishes_only_once():
    stopped = Event()
    ctx = FakeContext(stopped)
    ctx.stop_on_navigation = False

    def on_wait(page):
        page.close()
        stopped.set()

    ctx.on_wait = on_wait
    trace = record_http(ORIGIN + "/catalog", stopped,
                        factory=lambda: FakePlaywright(ctx), max_seconds=2)
    assert trace.end_reason == "button"
    assert len(trace.exchanges) == 1
    trace.destroy()


def test_crash_is_not_mistaken_for_manual_window_close():
    stopped = Event()
    ctx = FakeContext(stopped)
    ctx.stop_on_navigation = False
    ctx.on_wait = lambda page: page.crash()
    with pytest.raises(RecorderError, match="capture_interrupted"):
        record_http(ORIGIN + "/catalog", stopped,
                    factory=lambda: FakePlaywright(ctx), max_seconds=2)


def test_browser_disconnect_without_tab_close_is_error():
    stopped = Event()
    ctx = FakeContext(stopped)
    ctx.stop_on_navigation = False
    ctx.connected = True

    def on_wait(_page):
        ctx.connected = False

    ctx.on_wait = on_wait
    with pytest.raises(RecorderError, match="capture_interrupted"):
        record_http(ORIGIN + "/catalog", stopped,
                    factory=lambda: FakePlaywright(ctx), max_seconds=2)


def test_normal_close_seals_unfinished_requests_without_body_reads():
    trace = PrivateHttpTrace((ORIGIN,))
    pending = Request(ORIGIN + "/api/private?token=FICTIONAL_PRIVATE")
    trace.request(pending)
    response = Response(pending, data=b'{"account":"SECRET"}')
    trace.response(response)
    trace.finish_open_requests()
    assert trace.omitted["unfinished"] == 1
    assert trace.exchanges[0].response_body_state == "unfinished"
    assert trace.exchanges[0].response_body is None
    assert response.body_reads == 0
    assert not trace._pending and not trace._responses
    trace.destroy()


def test_last_window_close_while_paused_never_restores_dropped_bodies():
    from browser_recorder_http import CapturePause
    control = CapturePause()
    trace = PrivateHttpTrace((ORIGIN,), pause_control=control)
    req = Request(ORIGIN + "/private?token=FICTIONAL")
    trace.request(req)
    control.set_paused(True)
    trace._is_paused()
    trace.finish_open_requests()
    assert trace.omitted["paused"] == 1
    assert trace.omitted["unfinished"] == 0
    assert not trace._pending
    assert trace.exchanges[0].request_body is None
    trace.destroy()


@pytest.mark.parametrize("untrusted_size", [None, -1, True, 2**50, "12"])
def test_missing_content_length_never_reads_without_bounded_chromium_size(untrusted_size):
    trace = PrivateHttpTrace((ORIGIN,), max_body=64)
    req = Request(ORIGIN + "/fake-chunk")
    response = Response(req, data=b'{"BookId":214}', content_length=False)
    req.sizes = lambda: {"responseBodySize": untrusted_size}
    emit(trace, req, response)
    assert response.body_reads == 0
    assert trace.exchanges[0].response_body is None
    assert trace.exchanges[0].response_body_state == "body_omitted"
    assert trace.omitted["unknown_size"] == (0 if untrusted_size == 2**50 else 1)
    if untrusted_size == 2**50:
        assert trace.omitted["oversize"] == 1
    trace.destroy()


def test_missing_content_length_small_uncompressed_json_html_xml_are_captured():
    trace = PrivateHttpTrace((ORIGIN,), max_body=64)
    samples = [
        ("application/json", b'{"BookId":214}'),
        ("text/html", b"<html><p>fixture</p></html>"),
        ("application/xml", b"<books><BookId>214</BookId></books>"),
    ]
    for index, (mime, body) in enumerate(samples):
        req = Request(ORIGIN + "/fixture-" + str(index))
        response = Response(req, mime=mime, data=body, content_length=False)
        emit(trace, req, response)
        assert response.body_reads == 1
        assert trace.exchanges[index].response_body == body.decode("utf-8")
        assert trace.exchanges[index].response_body_state == "captured"
    assert trace.omitted["unknown_size"] == 0
    trace.destroy()


def test_fake_small_content_length_cannot_override_large_actual_body():
    trace = PrivateHttpTrace((ORIGIN,), max_body=64)
    req = Request(ORIGIN + "/bounded")
    response = Response(req, data=b"A" * 200, headers={"content-length": "1"})
    emit(trace, req, response)
    assert response.body_reads == 0
    assert trace.exchanges[0].response_body is None
    assert trace.omitted["oversize"] == 1
    trace.destroy()


def test_compressed_without_content_length_is_omitted_before_body_read():
    trace = PrivateHttpTrace((ORIGIN,), max_body=64)
    req = Request(ORIGIN + "/compressed")
    response = Response(
        req, data=b"tiny-wire-size", content_length=False,
        headers={"content-encoding": "gzip"},
    )
    emit(trace, req, response)
    assert response.body_reads == 0
    assert trace.omitted["compressed"] == 1
    assert trace.exchanges[0].response_body_state == "body_omitted"
    trace.destroy()


@pytest.mark.parametrize("encoding", ["gzip", "gzip\\nunsafe", "", "br, identity", 7])
def test_malformed_or_filtered_content_encoding_never_allows_body_read(encoding):
    trace = PrivateHttpTrace((ORIGIN,), max_body=64)
    req = Request(ORIGIN + "/compression")
    response = Response(req, data=b'{"BookId":214}', content_length=False)
    response.headers["content-encoding"] = encoding
    emit(trace, req, response)
    assert response.body_reads == 0
    assert trace.omitted["compressed"] == 1
    assert trace.exchanges[0].response_body is None
    trace.destroy()


@pytest.mark.parametrize("length", ["not-a-number", "1\\nunsafe", "-1", 10**100])
def test_filtered_invalid_content_length_cannot_masquerade_as_absent(length):
    trace = PrivateHttpTrace((ORIGIN,), max_body=64)
    req = Request(ORIGIN + "/malformed")
    response = Response(req, data=b"small")
    response.headers["content-length"] = length
    emit(trace, req, response)
    assert response.body_reads == 0
    assert trace.omitted["unknown_size"] == 1
    trace.destroy()


@pytest.mark.parametrize("actual", [None, True, -1, "14", 1000])
def test_chunked_post_untrusted_browser_counter_is_never_read(actual):
    trace = PrivateHttpTrace((ORIGIN,), max_body=64)
    request = Request(
        ORIGIN + "/upload", method="POST",
        headers={"content-type": "application/json"},
        data=b'{"BookId":214}',
    )
    request.sizes = lambda: {"requestBodySize": actual, "responseBodySize": 0}
    emit(trace, request, Response(request, data=b""))
    assert request.read_post_data == 0
    assert trace.exchanges[0].request_body is None
    assert trace.exchanges[0].request_body_state == "body_omitted"
    trace.destroy()


def test_chunked_post_measured_body_keeps_json_without_automatic_replay():
    trace = PrivateHttpTrace((ORIGIN,), max_body=64)
    request = Request(
        ORIGIN + "/upload", method="POST",
        headers={"content-type": "application/json"},
        data=b'{"BookId":214}',
    )
    emit(trace, request, Response(request, data=b""))
    assert request.read_post_data == 1
    assert trace.exchanges[0].request_body == '{"BookId":214}'
    assert trace.exchanges[0].request_body_state == "captured"
    trace.destroy()


@pytest.mark.parametrize("header_key,raw_value", [
    ("content-encoding", "gzip"),
    ("content-encoding", "gzip\\ninjected"),
    ("content-length", "invalid"),
    ("content-length", "1\\ninvalid"),
])
def test_post_with_unsafe_raw_header_is_never_read(header_key, raw_value):
    trace = PrivateHttpTrace((ORIGIN,), max_body=64)
    request = Request(
        ORIGIN + "/unsafe-post", method="POST",
        headers={"content-type": "application/json", header_key: raw_value},
        data=b'{"BookId":214}',
    )
    emit(trace, request, Response(request, data=b""))
    assert request.read_post_data == 0
    assert trace.exchanges[0].request_body is None
    trace.destroy()


def test_closed_capture_reasons_explain_omitted_bodies_without_reading_them():
    trace = PrivateHttpTrace((ORIGIN,), max_body=64)
    cases = [
        (Response(Request(ORIGIN + "/ok"), data=b'{"ok":true}'), "captured", 1),
        (Response(Request(ORIGIN + "/compressed"), data=b"small",
                  headers={"content-encoding": "gzip"}), "compressed", 0),
        (Response(Request(ORIGIN + "/binary"), mime="application/pdf",
                  data=b"PRIVATE_LICENSE"), "unsupported_mime", 0),
    ]
    for resp, reason, reads in cases:
        emit(trace, resp.request, resp)
        assert trace.exchanges[-1].response_body_reason == reason
        assert resp.body_reads == reads
    missing = Request(ORIGIN + "/unknown")
    response = Response(missing, data=b"small")
    missing.sizes = lambda: {}
    emit(trace, missing, response)
    assert response.body_reads == 0
    assert trace.exchanges[-1].response_body_reason == "size_unavailable"
    assert trace.omitted["unknown_size"] == 1
    invalid = Request(ORIGIN + "/invalid")
    response = Response(invalid, data=b"small",
                        headers={"content-length": "INVALID_PRIVATE"})
    emit(trace, invalid, response)
    assert response.body_reads == 0
    assert trace.exchanges[-1].response_body_reason == "unsafe_headers"
    assert trace.omitted["unknown_size"] == 2
    assert "PRIVATE_LICENSE" not in repr(trace)
    trace.destroy()


def test_closed_capture_reasons_distinguish_request_size_and_redirect():
    trace = PrivateHttpTrace((ORIGIN,), max_body=64)
    req = Request(ORIGIN + "/post", method="POST",
                  headers={"content-type": "application/json"},
                  data=b'{"id":1}')
    req.sizes = lambda: {"requestBodySize": None, "responseBodySize": 0}
    emit(trace, req, Response(req, status=302, data=b"",
                              headers={"location": "/other"}))
    entry = trace.exchanges[0]
    assert req.read_post_data == 0
    assert entry.request_body_reason == "size_unavailable"
    assert entry.response_body_reason == "redirect"
    assert entry.response_body_state == "redirect"
    trace.destroy()


@pytest.mark.parametrize("assets_first", (False, True))
def test_static_resource_flood_cannot_starve_late_json_or_post(assets_first):
    trace = PrivateHttpTrace((ORIGIN,))
    def assets():
        for n in range(1000):
            req = Request(ORIGIN + "/image/" + str(n))
            req.resource_type = "image"
            response = Response(req, mime="image/png", data=b"PNG")
            emit(trace, req, response)
            assert response.body_reads == 0
    def useful():
        for n in range(10):
            req = Request(ORIGIN + "/api/" + str(n))
            req.resource_type = "fetch"
            emit(trace, req, Response(req, data=b'{"ProductId":214}'))
        req = Request(ORIGIN + "/late-post", method="POST",
                      headers={"content-type":"application/json"}, data=b'{"page":2}')
        req.resource_type = "image"  # non-GET must never be sacrificed
        emit(trace, req, Response(req))
    for action in ((assets, useful) if assets_first else (useful, assets)):
        action()
    assert len(trace.exchanges) == 75
    assert trace.omitted["static_metadata_budget"] == 936
    assert trace.omitted["limit"] == 0
    assert sum(e.response_body is not None for e in trace.exchanges) == 11
    assert trace.exchanges[-1 if assets_first else 10].request_body == '{"page":2}'
    assert [e.sequence for e in trace.exchanges] == list(range(1, 76))
    coverage = trace.coverage()
    assert coverage["text_responses"] == 11
    assert coverage["binary_skipped"] == 64
    assert "api" not in repr(coverage) and ORIGIN not in repr(coverage)
    trace.destroy()


def test_excluded_origin_diagnostic_is_opt_in_bounded_and_never_reads_payload():
    class Excluded:
        url = FOREIGN + '/private-route?token=PRIVATE-QUERY'
        def __getattr__(self, _name):
            raise AssertionError('Excluded payload must never be read')

    default = PrivateHttpTrace((ORIGIN,))
    default.request(Excluded())
    assert default.origin_diagnostics() == ({}, 0)
    trace = PrivateHttpTrace((ORIGIN,), diagnose_origins=True)
    trace.request(Excluded())
    trace.request(Excluded())
    assert trace.origin_diagnostics() == ({FOREIGN: 2}, 0)
    assert not trace.exchanges
    for index in range(30):
        req = Excluded()
        req.url = f'https://cdn{index}.example.org/image?private=yes'
        trace.request(req)
    origins, overflow = trace.origin_diagnostics()
    assert len(origins) == 16 and overflow == 15
    assert trace.omitted['out_of_scope'] == 32
    assert 'PRIVATE-QUERY' not in repr(origins) and 'private-route' not in repr(origins)
    assert FOREIGN not in repr(trace) and FOREIGN not in repr(trace.coverage())
    origins.clear()  # the caller never mutates the source inventory
    assert len(trace.origin_diagnostics()[0]) == 16
    trace.paused = True
    trace.request(Excluded())
    assert trace.origin_diagnostics()[0][FOREIGN] == 2
    trace.destroy()
    assert trace.origin_diagnostics() == ({}, 0)


def test_scripts_share_static_budget_without_discarding_fetch_or_post():
    trace = PrivateHttpTrace((ORIGIN,))
    for index in range(1000):
        req = Request(ORIGIN + '/script/' + str(index))
        req.resource_type = 'script'
        emit(trace, req, Response(req, mime='application/javascript', data=b'void 0;'))
    for index in range(10):
        req = Request(ORIGIN + '/api/' + str(index))
        req.resource_type = 'fetch'
        emit(trace, req, Response(req))
    post = Request(ORIGIN + '/submit', method='POST')
    post.resource_type = 'script'
    emit(trace, post, Response(post))
    assert len(trace.exchanges) == 75
    assert trace.omitted['static_metadata_budget'] == 936
    assert trace.omitted['limit'] == 0
    assert trace.exchanges[-1].method == 'POST'
    assert all(e.response_body is not None for e in trace.exchanges)
    trace.destroy()


def test_origin_inventory_is_absent_from_vault_and_private_zip(tmp_path):
    from browser_recorder_vault import PrivateSessionVault
    from browser_recorder_private_zip import save_session_llm_zip
    from zipfile import ZipFile
    from io import BytesIO
    pytest.importorskip('cryptography')
    trace = PrivateHttpTrace((ORIGIN,), diagnose_origins=True)
    trace.request(Request(FOREIGN + '/private?token=PRIVATE-QUERY'))
    req = Request(ORIGIN + '/api')
    emit(trace, req, Response(req))
    vault = PrivateSessionVault(tmp_path / 'vault')
    writer = vault.begin('FICTIONAL-VAULT-PASSPHRASE')
    writer.save(trace, 'completed', force=True)
    stored = vault.open(trace.vault_session_id, 'FICTIONAL-VAULT-PASSPHRASE')
    assert stored.trace.origin_diagnostics() == ({}, 0)
    destination = tmp_path / 'private.zip'
    save_session_llm_zip(stored, destination, confirmed=True)
    with ZipFile(destination) as zipped:
        assert all(FOREIGN.encode() not in zipped.read(name) for name in zipped.namelist())
    from browser_recorder_review import IdentifierReview
    from browser_recorder_zip import prepare_reviewed_zip
    plan = prepare_reviewed_zip(IdentifierReview(trace))
    assert FOREIGN not in repr(plan.preview())
    stored.trace.destroy()
    trace.destroy()


def test_static_scripts_cannot_exhaust_body_budget_before_api_responses():
    trace = PrivateHttpTrace((ORIGIN,))
    reads = 0
    for index in range(64):
        req = Request(ORIGIN + '/script/' + str(index))
        req.resource_type = 'script'
        response = Response(req, mime='application/javascript', data=b'x' * DEFAULT_BODY_BYTES)
        emit(trace, req, response)
        reads += response.body_reads
    assert reads == 2  # static bodies use at most 64 KiB / one quarter
    assert trace.omitted['oversize'] == 62
    req = Request(ORIGIN + '/api/late')
    req.resource_type = 'fetch'
    emit(trace, req, Response(req, data=b'{"ok":true}'))
    assert trace.exchanges[-1].response_body == '{"ok":true}'
    trace.destroy()


@pytest.mark.parametrize("encoding", ["gzip", "br", "deflate"])
def test_compressed_opt_in_keeps_small_decoded_response_with_wire_size_guard(encoding):
    decoded = b'{"ProductId":214,"accountId":"FICTIONAL_77"}'
    trace = PrivateHttpTrace((ORIGIN,), max_body=64,
                             allow_compressed_responses=True)
    req = Request(ORIGIN + "/api/books")
    resp = Response(req, data=decoded, headers={
        "content-encoding": encoding, "content-length": "18",
    })
    # Playwright reports transfer bytes in request.sizes(), but body()
    # returns Chromium-decoded bytes. The fake models both independently.
    req._response_size = 18
    emit(trace, req, resp)
    assert resp.body_reads == 1
    assert trace.exchanges[0].response_body == decoded.decode()
    assert trace.exchanges[0].response_body_reason == "captured"
    assert trace.omitted["compressed"] == 0
    trace.destroy()


def test_compressed_opt_in_still_blocks_binary_untrusted_headers_and_large_wire():
    trace = PrivateHttpTrace((ORIGIN,), max_body=64,
                             allow_compressed_responses=True)
    for encoding in ("gzip, br", "invalid", ""):
        req = Request(ORIGIN + "/unsupported")
        resp = Response(req, data=b"{}", headers={"content-encoding": encoding})
        emit(trace, req, resp)
        assert resp.body_reads == 0
        assert trace.exchanges[-1].response_body_reason == "compressed"
    req = Request(ORIGIN + "/binary")
    resp = Response(req, mime="application/pdf", data=b"PRIVATE_LICENSE",
                    headers={"content-encoding": "gzip"})
    emit(trace, req, resp)
    assert resp.body_reads == 0
    assert trace.exchanges[-1].response_body_state == "binary_skipped"
    req = Request(ORIGIN + "/large-encoded")
    resp = Response(req, data=b"small", headers={
        "content-encoding": "gzip", "content-length": "999",
    })
    req._response_size = 999
    emit(trace, req, resp)
    assert resp.body_reads == 0
    assert trace.exchanges[-1].response_body_reason == "over_budget"
    trace.destroy()


def test_compressed_opt_in_decoded_size_excess_is_not_persisted():
    trace = PrivateHttpTrace((ORIGIN,), max_body=64,
                             allow_compressed_responses=True)
    req = Request(ORIGIN + "/gzip-bomb")
    resp = Response(req, data=b"x" * 128, headers={
        "content-encoding": "gzip", "content-length": "12",
    })
    req._response_size = 12
    emit(trace, req, resp)
    assert resp.body_reads == 1  # opt-in accepts pre-retention memory risk
    assert trace.exchanges[0].response_body is None
    assert trace.exchanges[0].response_body_reason == "over_budget"
    assert trace.omitted["oversize"] == 1
    assert trace.total_body == 768 * 1024
    trace.destroy()


def test_compressed_opt_in_cannot_skip_measured_byte_guard():
    trace = PrivateHttpTrace((ORIGIN,), allow_compressed_responses=True)
    req = Request(ORIGIN + "/unknown")
    req.sizes = lambda: {}
    resp = Response(req, headers={"content-encoding": "br"})
    emit(trace, req, resp)
    assert resp.body_reads == 0
    assert trace.exchanges[0].response_body_reason == "size_unavailable"
    with pytest.raises(RecorderError, match="invalid_capture_strategy"):
        PrivateHttpTrace((ORIGIN,), allow_compressed_responses="yes")
    trace.destroy()


@pytest.mark.parametrize("limit", [32768, 65536, 131072])
def test_text_body_limit_is_explicit_bounded_and_default_unchanged(limit):
    body = b'{"Title":"' + b"x" * 70000 + b'"}'
    req = Request(ORIGIN + "/large-json")
    response = Response(req, data=body)
    trace = PrivateHttpTrace((ORIGIN,), max_body=limit)
    emit(trace, req, response)
    assert trace.max_body == limit
    assert trace.coverage()["max_body"] == limit
    assert (trace.exchanges[0].response_body is not None) == (limit >= len(body))
    if limit < len(body):
        assert response.body_reads == 0
        assert trace.exchanges[0].response_body_reason == "over_budget"
    else:
        assert response.body_reads == 1
        assert len(trace.exchanges[0].response_body.encode()) == len(body)
    trace.destroy()

    default = PrivateHttpTrace((ORIGIN,))
    assert default.max_body == DEFAULT_BODY_BYTES == 32768
    assert MAX_BODY_BYTES == 131072
    default.destroy()


@pytest.mark.parametrize("invalid", [0, 1, 32767, 32000, 262144, "65536", True, None])
def test_record_http_rejects_unsupported_body_limit_before_browser(invalid):
    with pytest.raises(RecorderError, match="invalid_capture_strategy"):
        record_http(ORIGIN + "/catalog", Event(), max_body=invalid)


def test_record_http_forwards_selected_body_limit_to_memory_trace():
    stopped = Event()
    stopped.set()
    runtime = FakePlaywright(FakeContext(stopped))
    trace = record_http(
        ORIGIN + "/catalog", stopped, factory=lambda: runtime, max_body=131072,
    )
    try:
        assert trace.max_body == 131072
    finally:
        trace.destroy()


def test_evidence_counts_capture_usefulness_without_exposing_private_values():
    trace = PrivateHttpTrace((ORIGIN,))
    ok = Request(ORIGIN + "/json-ok?private=NEVER_ECHO")
    emit(trace, ok, Response(ok, data=b'{"ok":true}'))
    large = Request(ORIGIN + "/json-large")
    big = Response(large, data=b"A" * 40000)
    emit(trace, large, big)
    assert big.body_reads == 0
    unknown = Request(ORIGIN + "/json-unknown")
    unknown.sizes = lambda: {"requestBodySize": 0, "responseBodySize": 0}
    bad = Response(unknown, data=b'{"private":"SECRET"}')
    emit(trace, unknown, bad)
    assert bad.body_reads == 0
    html = Request(ORIGIN + "/page")
    emit(trace, html, Response(html, mime="text/html", data=b"<html></html>"))
    post = Request(
        ORIGIN + "/save", method="POST",
        headers={"content-type": "application/json"},
        data=b'{"id":214}',
    )
    emit(trace, post, Response(post, mime="text/plain", data=b"ok"))
    evidence = trace.coverage()
    assert {key: evidence[key] for key in (
        "json_responses_observed", "json_responses_captured",
        "html_responses_observed", "html_responses_captured",
        "write_requests_observed", "write_requests_captured",
        "response_size_unavailable", "response_over_budget",
    )} == {
        "json_responses_observed": 3,
        "json_responses_captured": 1,
        "html_responses_observed": 1,
        "html_responses_captured": 1,
        "write_requests_observed": 1,
        "write_requests_captured": 1,
        "response_size_unavailable": 1,
        "response_over_budget": 1,
    }
    assert all(type(value) is int for value in evidence.values())
    assert "NEVER_ECHO" not in repr(evidence)
    assert "SECRET" not in repr(evidence)
    from browser_recorder_review import IdentifierReview
    from browser_recorder_zip import prepare_reviewed_zip
    import json
    review = IdentifierReview(trace)
    try:
        for candidate in review.candidates():
            review.decide(candidate.key, "anonymize")
        plan = prepare_reviewed_zip(review)
        members = {row["name"]: row["text"] for row in plan.preview()["members"]}
        for path in ("coverage-report.json", "manifest.json"):
            emitted = json.loads(members[path])["capture_evidence"]
            assert emitted["json_responses_captured"] == 1
            assert emitted["response_size_unavailable"] == 1
            assert emitted["response_over_budget"] == 1
            assert all(type(value) is int for value in emitted.values())
        assert "NEVER_ECHO" not in repr(members["coverage-report.json"])
        assert "SECRET" not in repr(members["coverage-report.json"])
    finally:
        review.destroy()
        trace.destroy()


def test_evicted_playwright_response_body_is_isolated_and_later_json_survives(caplog):
    """A Playwright Error from response.body() cannot escape the callback.

    This mimics Network.getResponseBody having no resource after a navigation.
    The free-text error is intentionally private and must not reach logging.
    """
    trace = PrivateHttpTrace((ORIGIN,))
    request = Request(ORIGIN + "/api/first")
    response = Response(request, data=b'{"ProductId":214}')
    def evicted_body():
        response.body_reads += 1
        raise RuntimeError("Network.getResponseBody: PRIVATE_TOKEN_OR_URL")
    response.body = evicted_body

    emit(trace, request, response)  # No exception may escape into pyee.
    first = trace.exchanges[0]
    assert first.status == 200 and first.mime == "application/json"
    assert first.response_body is None
    assert first.response_body_state == "body_omitted"
    assert first.response_body_reason == "read_failed"
    assert not first.network_error
    assert response.body_reads == 1
    assert "PRIVATE_TOKEN_OR_URL" not in caplog.text
    assert "PRIVATE_TOKEN_OR_URL" not in repr(trace)

    following = Request(ORIGIN + "/api/next")
    result = Response(following, data=b'{"BookId":214}')
    emit(trace, following, result)
    assert result.body_reads == 1
    assert trace.exchanges[1].response_body == '{"BookId":214}'
    assert len(trace.exchanges) == 2
    assert not trace._pending and not trace._responses
    trace.destroy()
