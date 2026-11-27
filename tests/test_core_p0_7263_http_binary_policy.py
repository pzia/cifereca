"""#7263: offline capture policy and both ZIP consumers."""
import gzip
import json
import zipfile

import httpx
import pytest

from cifereca.http_trace import HTTPTraceCapture, TraceHTTPTransport
from cifereca.logging_utils import diagnostics_level, set_diagnostics_level
from cifereca.core.capture_http_window import capture_http_window
from cifereca.core.capture_session_export import export_capture_session, validate_capture_session


@pytest.fixture(autouse=True)
def trace_level():
    previous = diagnostics_level()
    set_diagnostics_level("trace")
    try:
        yield
    finally:
        set_diagnostics_level(previous)


def _client(capture, handler):
    return httpx.Client(transport=TraceHTTPTransport(
        httpx.MockTransport(handler), capture=capture, producer="synthetic",
    ), trust_env=False)


def test_binary_never_charges_body_budget_or_changes_delivery():
    capture = HTTPTraceCapture(include_binary=False, session_limit=4096)
    image = b"\xff\xd8\xff" + b"\x00" * 10000
    compressed = gzip.compress(b'{"document": true}')

    def handler(request):
        if request.url.path == "/image":
            assert request.content == image
            return httpx.Response(200, headers={"content-type": "image/jpeg"},
                                  stream=httpx.ByteStream(image))
        return httpx.Response(200, headers={"content-type": "application/json",
                                           "content-encoding": "gzip"},
                              stream=httpx.ByteStream(compressed))

    with _client(capture, handler) as client:
        assert client.post("https://example.test/image", content=iter([image]),
                           headers={"content-type": "image/jpeg"}).content == image
        assert client.get("https://example.test/json").json() == {"document": True}

    snapshot = capture.snapshot()
    first, second = snapshot["exchanges"]
    for side in ("request_body", "response_body"):
        assert first[side]["state"] == "binary_excluded"
        assert first[side]["observed_bytes"] == len(image)
        assert not first[side]["data"]
    assert second["response_body"]["state"] == "complete"
    assert second["response_body"]["data"] == compressed
    assert snapshot["used_bytes"] < 4096


@pytest.mark.parametrize(("mime", "data", "state"), [
    ("image/svg+xml", b"<svg/>", "binary_excluded"),
    ("application/epub+zip", b"PK\x03\x04abc", "binary_excluded"),
    ("application/octet-stream", b"%PDF-1.7", "binary_excluded"),
    ("", b"\x89PNG\r\n\x1a\n", "binary_excluded"),
    ("", b'{"ok":true}', "complete"),
    ("application/octet-stream", b"<html>ok</html>", "complete"),
    ("application/vnd.adobe.adept+xml", b"<acsm/>", "complete"),
    ("text/plain", b"readable text", "complete"),
    ("", b"ambiguous", "body_type_unknown"),
])
def test_mime_and_bounded_signature(mime, data, state):
    capture = HTTPTraceCapture(include_binary=False)

    class Split(httpx.SyncByteStream):
        def __iter__(self):
            for byte in data:
                yield bytes((byte,))

    with _client(capture, lambda _: httpx.Response(
            200, headers={"content-type": mime} if mime else {}, stream=Split(),
    )) as client:
        assert client.get("https://example.test").content == data
    body = capture.snapshot()["exchanges"][0]["response_body"]
    assert body["state"] == state
    assert body["observed_bytes"] == len(data)
    assert body["data"] == (data if state == "complete" else b"")


def test_both_exports_filter_history_and_preserve_window(tmp_path):
    capture = HTTPTraceCapture()
    initial = capture.status()
    with _client(capture, lambda _: httpx.Response(
        200, headers={"content-type": "application/pdf"}, content=b"%PDF-1.7",
    )) as client:
        client.get("https://example.test")
    before = capture.snapshot()
    assert before["exchanges"][0]["response_body"]["data"] == b"%PDF-1.7"
    capture.set_include_binary(False)
    manual = capture.export(tmp_path / "manual.zip")
    snapshot = capture_http_window(capture.snapshot(), initial)
    journal = {
        "format": "cifereca-capture-session-journal", "raw": b"",
        "coverage": "complete", "session_ref": "x", "process_id": 1,
        "started_at_utc": "2026-01-01T00:00:00Z",
        "finished_at_utc": "2026-01-01T00:00:01Z",
        "record_count": 0, "omitted_records": 0,
    }
    automatic = export_capture_session(tmp_path, journal=journal,
                                       http_snapshot=snapshot, app_exit_code=0)
    for path, prefix in ((manual, ""), (automatic, "http/")):
        with zipfile.ZipFile(path) as archive:
            index = json.loads(archive.read(prefix + "index.json"))
            entry = index["exchanges"][0]
            body = entry["response_body"]
            assert body["state"] == "binary_excluded"
            assert body["prior_state"] == "complete"
            assert body["stored_bytes"] == 0
            assert "blob" not in body and "sha256" not in body
            assert prefix + "bodies/" + entry["exchange_ref"] + "-response.bin" not in archive.namelist()
    assert validate_capture_session(automatic)["status"] == "valid"
    assert capture.status()["used_bytes"] == before["used_bytes"]
    capture.set_include_binary(True)
    assert capture.snapshot()["exchanges"][0]["response_body"]["data"] == b"%PDF-1.7"


def test_preference_is_persistent_and_only_diagnostics_page_can_change_it(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from cifereca import http_trace, paths
    from cifereca.brokers import DiagnosticsBrokerFactory
    from cifereca.config import Config
    from cifereca.plugin_api import DiagnosticsHttpPolicyBroker

    cfg = Config(tmp_path / "config.json")
    assert http_trace.http_capture_include_binary(cfg) is True
    capture = HTTPTraceCapture()
    monkeypatch.setattr(http_trace, "HTTP_TRACE", capture)
    monkeypatch.setattr(paths, "acquisition_lock_file", lambda: tmp_path / "capture.lock")
    factory = DiagnosticsBrokerFactory(SimpleNamespace(config=cfg))
    other = factory.for_plugin("provider.example")
    assert not isinstance(other, DiagnosticsHttpPolicyBroker)
    assert not hasattr(other, "for_plugin")
    diagnostics = factory.for_plugin("ui.page.diagnostics")
    assert isinstance(diagnostics, DiagnosticsHttpPolicyBroker)
    with pytest.raises(ValueError):
        diagnostics.set_http_trace_include_binary("false")
    assert diagnostics.set_http_trace_include_binary(False) is False
    assert capture.status()["include_binary"] is False
    reloaded = Config(cfg.path)
    assert http_trace.http_capture_include_binary(reloaded) is False
    draft = {"diagnostics": {"level": "trace", "http_capture_include_binary": True}}
    assert http_trace.preserve_http_capture_preference(draft, current=reloaded)["diagnostics"] == {
        "level": "trace", "http_capture_include_binary": False,
        "capture_retention_days": 90, "capture_retention_bytes": 128 * 1024 * 1024,
    }
    assert diagnostics.set_http_trace_include_binary(True) is True
    assert http_trace.http_capture_include_binary(Config(cfg.path)) is True


def test_unknown_partial_does_not_export_pending_probe(tmp_path):
    capture = HTTPTraceCapture(include_binary=False)

    class Split(httpx.SyncByteStream):
        def __iter__(self):
            yield b"ambiguous"
            yield b" bytes"

    with _client(capture, lambda _: httpx.Response(200, stream=Split())) as client:
        with client.stream("GET", "https://example.test") as response:
            assert next(response.iter_raw()) == b"ambiguous"
    body = capture.snapshot()["exchanges"][0]["response_body"]
    assert body["state"] == "body_type_unknown"
    assert body["prior_state"] == "partial"
    assert body["observed_bytes"] == 9
    assert body["data"] == b""


def test_content_disposition_excludes_binary_without_mime():
    capture = HTTPTraceCapture(include_binary=False)
    with _client(capture, lambda _: httpx.Response(
            200, headers={"content-disposition": 'attachment; filename="book.epub"'},
            stream=httpx.ByteStream(b"opaque"),
    )) as client:
        assert client.get("https://example.test").content == b"opaque"
    assert capture.snapshot()["exchanges"][0]["response_body"]["state"] == "binary_excluded"


def test_declared_brotli_text_is_not_mistaken_for_binary():
    capture = HTTPTraceCapture(include_binary=False)
    payload = b"\x00\xaa\xbb\xcc"  # opaque transport fixture; no decoding claimed
    with _client(capture, lambda _: httpx.Response(
            200, headers={"content-type": "text/html", "content-encoding": "br"},
            stream=httpx.ByteStream(payload),
    )) as client:
        with client.stream("GET", "https://example.test") as response:
            assert b"".join(response.iter_raw()) == payload
    body = capture.snapshot()["exchanges"][0]["response_body"]
    assert body["state"] == "complete"
    assert body["data"] == payload
