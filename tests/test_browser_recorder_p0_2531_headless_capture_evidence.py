"""Core-collectable fixture for the Browser Recorder P0 Chromium evidence gate.

Native GTK/Chromium assertions reside in test_browser_recorder_installed_chromium_e2e;
this file remains runnable in generic CI lacking those optional dependencies.
No real endpoints or credentials; only a fictional HTTPS fixture.
"""
from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "src/plugins/browser-recorder"
CHROMIUM_TEST = ROOT / "tests/test_browser_recorder_installed_chromium_e2e.py"


def test_chromium_e2e_declares_positive_body_and_vault_evidence():
    """Prevent the real-browser gate from silently reverting to status-only."""
    tree = ast.parse(CHROMIUM_TEST.read_text(encoding="utf-8"))
    tests = {
        item.name: item for item in tree.body
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    test = tests["test_installed_plugin_drives_real_chromium_to_confirmed_zip"]
    body = ast.unparse(test)
    for expected in (
        "write_requests_captured", "json_responses_captured",
        "html_responses_captured", "response_read_failed",
        "request_read_failed", "response_unfinished",
        "request_unfinished", "stored.trace.coverage()",
    ):
        assert expected in body
    assert "stored.trace.destroy()" in body


def test_loss_is_separate_from_capture_usefulness_without_private_values(
    monkeypatch,
):
    monkeypatch.syspath_prepend(str(PLUGIN))
    from browser_recorder_http import PrivateHttpTrace

    class Request:
        method = "POST"
        resource_type = "fetch"

        def __init__(self):
            self.url = "https://fictional.example.org/login?token=PRIVATE_URL"
            self.headers = {"content-type": "application/json"}
            self._post_reads = 0
            self._response_bytes = 18

        @property
        def post_data_buffer(self):
            self._post_reads += 1
            raise RuntimeError("FICTIONAL_PRIVATE_CREDENTIAL")

        def sizes(self):
            return {"requestBodySize": 13,
                    "responseBodySize": self._response_bytes}

    class Response:
        status = 200
        def __init__(self, request):
            self.request = request
            self.data = b'{"ok":true}'
            request._response_bytes = len(self.data)
            self.headers = {
                "content-type": "application/json",
                "content-length": str(len(self.data)),
            }
        def body(self):
            return self.data

    trace = PrivateHttpTrace(("https://fictional.example.org",))
    try:
        req = Request()
        trace.request(req)
        trace.response(Response(req))
        trace.finished(req)
        evidence = trace.coverage()
        assert evidence["write_requests_observed"] == 1
        assert evidence["write_requests_captured"] == 0
        assert evidence["request_read_failed"] == 1
        assert evidence["json_responses_captured"] == 1
        assert evidence["response_read_failed"] == 0
        assert evidence["response_unfinished"] == 0
        assert evidence["text_responses"] == 1
        assert req._post_reads == 1
        assert "PRIVATE_URL" not in repr(evidence)
        assert "PRIVATE_CREDENTIAL" not in repr(evidence)
    finally:
        trace.destroy()
