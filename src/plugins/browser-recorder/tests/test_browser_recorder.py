from __future__ import annotations

from pathlib import Path
import json
import os
import stat
from threading import Event, Semaphore

import pytest

from browser_recorder import (
    Capture, RecorderError, load_capture, record, replay, replayable,
    safe_url, save_capture, site_origin, MAX_EVENTS, MAX_BYTES,
)

ORIGIN = "https://bibliotheque.example.org"


@pytest.mark.parametrize("url", [
    "http://bibliotheque.example.org", "https://localhost",
    "https://127.0.0.1", "https://user:pass@bibliotheque.example.org",
    "https://bibliotheque.example.org:8443", "file:///etc/passwd",
    "https://bibliotheque.example.org\n.evil.test",
])
def test_non_public_or_unsafe_origin_rejected(url):
    with pytest.raises(RecorderError):
        site_origin(url)


def test_private_material_never_in_har():
    session = Capture(ORIGIN, limit=2)
    class Request:
        method = "POST"
        url = ORIGIN + "/users/alexandre/borrowing/123?token=SECRET&email=a@b.c#fragment"
        @property
        def headers(self):
            raise AssertionError("Never access request headers")
        @property
        def post_data(self):
            raise AssertionError("Never access request bodies")
    request = Request()
    class Response:
        status = 302
        def __init__(self, request):
            self.request = request
        @property
        def headers(self):
            raise AssertionError("Never access response headers")
        @property
        def body(self):
            raise AssertionError("Never access response bodies")
    session.request(request)
    session.response(Response(request))
    session.navigate(request.url)
    document = json.dumps(session.document())
    for secret in ("SECRET", "alexandre", "/123", "token=", "email=", "a@b.c", "fragment", "postData"):
        assert secret not in document
    assert "POST" in document and "302" in document
    assert "~redacted~" in document
    exported = session.document()["log"]["entries"][0]
    assert exported["request"]["cookies"] == []
    assert exported["response"]["cookies"] == []
    assert exported["request"]["headers"] == []
    assert exported["response"]["headers"] == []


def test_bounded_capture_only_same_origin():
    session = Capture(ORIGIN, limit=1)
    class Request:
        method = "GET"
        url = ORIGIN + "/books"
    session.request(Request())
    session.request(Request())
    session.request(type("Foreign", (), {"url": "https://external.example/secret", "method": "GET"})())
    assert len(session.entries) == 1
    assert session.dropped == 1
    session.navigate("https://external.example/anything")
    assert not session.navigations


@pytest.mark.parametrize("url", [
    ORIGIN + "/books?token=123", ORIGIN + "/books#p",
    ORIGIN + "/books/~redacted~", ORIGIN + "/account/delete",
    ORIGIN + "/logout", "https://other.example.org/books",
])
def test_replay_not_permitted_for_sensitive_or_dynamic_routes(url):
    assert not replayable(url, ORIGIN)


def test_allowed_navigation_is_strictly_static():
    assert replayable(ORIGIN + "/library/books", ORIGIN)
    assert safe_url(ORIGIN + "/library/abc42?code=abc", ORIGIN) == ORIGIN + "/library/~redacted~"
    assert not replayable(ORIGIN + "/library/~redacted~", ORIGIN)


def test_private_har_and_origin_bound_replay(tmp_path):
    capture = Capture(ORIGIN)
    capture.navigate(ORIGIN + "/library")
    capture.navigate(ORIGIN + "/account/SECRET?token=abc")
    capture.navigate(ORIGIN + "/books")
    name = save_capture(tmp_path, capture)
    assert name.endswith(".har")
    assert not stat.S_IMODE((tmp_path / name).stat().st_mode) & 0o077
    assert load_capture(tmp_path, name, ORIGIN) == [ORIGIN + "/library", ORIGIN + "/books"]
    with pytest.raises(RecorderError, match="capture_origin_mismatch"):
        load_capture(tmp_path, name, "https://another.example.org")


def test_invalid_and_symlink_capture_files_are_rejected(tmp_path):
    capture = Capture(ORIGIN)
    name = save_capture(tmp_path, capture)
    for invalid in ("../../other.har", "trace.har", ".har", name + "/../secrets"):
        with pytest.raises(RecorderError, match="invalid_filename"):
            load_capture(tmp_path, invalid, ORIGIN)
    (tmp_path / name).unlink()
    (tmp_path / name).symlink_to(tmp_path / "private.txt")
    with pytest.raises(RecorderError, match="read_failed"):
        load_capture(tmp_path, name, ORIGIN)
    redirected = tmp_path.parent / (tmp_path.name + "-redirect")
    redirected.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(RecorderError, match="unsafe_directory"):
        save_capture(redirected, capture)


def test_large_and_tampered_imports_fail_closed(tmp_path):
    session = Capture(ORIGIN)
    name = save_capture(tmp_path, session)
    (tmp_path / name).write_bytes(b"x" * (MAX_BYTES + 1))
    with pytest.raises(RecorderError, match="capture_limit"):
        load_capture(tmp_path, name, ORIGIN)
    tampered = session.document()
    tampered["log"]["_cifereca"]["navigations"] = [ORIGIN + "/books?secret=x"]
    (tmp_path / name).write_text(json.dumps(tampered))
    with pytest.raises(RecorderError, match="invalid_capture"):
        load_capture(tmp_path, name, ORIGIN)


class FakeRequest:
    method = "GET"
    def __init__(self, url):
        self.url = url


class FakeResponse:
    status = 200
    def __init__(self, request):
        self.request = request


class FakeFrame:
    def __init__(self, url):
        self.url = url


class FakePage:
    def __init__(self, context):
        self.context = context
        self.events = {}
        self.main_frame = FakeFrame("about:blank")
    def on(self, event, handler):
        self.events.setdefault(event, []).append(handler)
    def goto(self, url, **kwargs):
        self.context.visits.append(url)
        self.main_frame.url = url
        for callback in self.events.get("framenavigated", []):
            callback(self.main_frame)
        request = FakeRequest(url)
        for callback in self.context.events.get("request", []):
            callback(request)
        for callback in self.context.events.get("response", []):
            callback(FakeResponse(request))
        if self.context.record_stop is not None:
            self.context.record_stop.set()
    def wait_for_timeout(self, ms):
        return None


class FakeContext:
    def __init__(self, record_stop=None):
        self.events = {}
        self.visits = []
        self.record_stop = record_stop
    def on(self, event, handler):
        self.events.setdefault(event, []).append(handler)
    def new_page(self):
        page = FakePage(self)
        for handler in self.events.get("page", []):
            handler(page)
        return page
    def close(self):
        pass


class FakePW:
    def __init__(self, context):
        self.context = context
        self.chromium = self
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass
    def launch(self, **kwargs):
        return self
    def new_context(self, **kwargs):
        assert not kwargs["accept_downloads"]
        assert kwargs["service_workers"] == "block"
        return self.context
    def close(self):
        pass


def test_record_and_supervised_replay_offline(tmp_path):
    stop = Event()
    context = FakeContext(record_stop=stop)
    name = record(ORIGIN + "/library", tmp_path, stop, factory=lambda: FakePW(context))
    assert context.visits == [ORIGIN + "/library"]
    assert load_capture(tmp_path, name, ORIGIN) == [ORIGIN + "/library"]
    replay_context = FakeContext()
    advance = Semaphore(1)
    count = replay(tmp_path, name, ORIGIN, Event(), advance,
                   factory=lambda: FakePW(replay_context))
    assert count == 1
    assert replay_context.visits == [ORIGIN + "/", ORIGIN + "/library"]


def test_replay_requires_confirmation(tmp_path):
    cap = Capture(ORIGIN)
    cap.navigate(ORIGIN + "/books")
    name = save_capture(tmp_path, cap)
    stopped = Event()
    stopped.set()
    ctx = FakeContext()
    assert replay(tmp_path, name, ORIGIN, stopped, Semaphore(0),
                  factory=lambda: FakePW(ctx)) == 0
    assert ctx.visits == [ORIGIN + "/"]



def test_unconfirmed_stop_discards_all_capture_artifacts(tmp_path):
    stopped = Event()
    context = FakeContext(record_stop=stopped)
    save_requested = Event()
    with pytest.raises(RecorderError, match="capture_interrupted"):
        record(ORIGIN + "/library", tmp_path, stopped,
               save_requested=save_requested, factory=lambda: FakePW(context))
    assert not list(tmp_path.glob("*.har"))


def test_explicit_save_request_allows_har(tmp_path):
    stopped = Event()
    save_requested = Event()
    save_requested.set()
    context = FakeContext(record_stop=stopped)
    filename = record(ORIGIN + "/books", tmp_path, stopped,
                      save_requested=save_requested, factory=lambda: FakePW(context))
    assert (tmp_path / filename).is_file()


def test_exchange_ids_redirect_and_network_error_without_failure_text():
    capture = Capture(ORIGIN)
    first = FakeRequest(ORIGIN + "/books?secret=HIDDEN")
    second = FakeRequest(ORIGIN + "/account/HIDDEN")
    third = FakeRequest(ORIGIN + "/search")
    capture.request(first)
    capture.request(second)
    capture.request(third)
    redirect = FakeResponse(first)
    redirect.status = 302
    capture.response(redirect)
    capture.request_failed(second)
    ok = FakeResponse(third)
    capture.response(ok)
    entries = capture.document()["log"]["entries"]
    assert [e["_cifereca"]["exchange_id"] for e in entries] == [1, 2, 3]
    assert [e["_cifereca"]["outcome"] for e in entries] == [
        "redirect", "network_error", "response",
    ]
    assert [e["response"]["status"] for e in entries] == [302, 0, 200]
    assert "HIDDEN" not in json.dumps(entries)
    assert "secret=" not in json.dumps(entries)


def test_failure_without_recorded_request_is_ignored():
    capture = Capture(ORIGIN)
    capture.request_failed(FakeRequest("https://foreign.example/private"))
    assert capture.document()["log"]["entries"] == []
