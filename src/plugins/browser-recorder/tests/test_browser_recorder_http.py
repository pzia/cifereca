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
        assert kw["accept_downloads"] is False
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
