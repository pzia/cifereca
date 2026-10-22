"""Manual Browser Recorder binary saving: explicit choice, no trace/ZIP bytes."""
from __future__ import annotations

import os
from pathlib import Path
import pytest

from browser_recorder import RecorderError
import browser_recorder_download as saver
from browser_recorder_http import PrivateHttpTrace

ORIGIN = "https://fictional-download.example.org"
PRIVATE_BINARY = b"FICTIONAL-LICENSE-ONLY-ON-USER-DISK"


class FakeDownload:
    def __init__(self, body=PRIVATE_BINARY, fail=False):
        self.data, self.fail = body, fail
        self.saved = False

    @property
    def suggested_filename(self):
        raise AssertionError("Browser-supplied filename must never be accessed")

    @property
    def url(self):
        raise AssertionError("Download URL must not leave the browser context")

    def save_as(self, filename):
        self.saved = True
        if self.fail:
            raise RuntimeError("SENSITIVE_FAKE_URL_NEVER_LOG")
        Path(filename).write_bytes(self.data)


def test_manual_choice_writes_exact_0600_and_does_not_access_url(tmp_path):
    target = tmp_path / "chosen-fictional.acsm"
    download = FakeDownload()
    assert not target.exists() and not download.saved
    saver.save_chosen_download(download, target)
    assert download.saved
    assert target.read_bytes() == PRIVATE_BINARY
    assert target.stat().st_mode & 0o777 == 0o600
    assert sorted(path.name for path in tmp_path.iterdir()) == [target.name]
    with pytest.raises(RecorderError, match="download_save_failed"):
        saver.save_chosen_download(FakeDownload(b"SECOND"), target)
    assert target.read_bytes() == PRIVATE_BINARY  # O_EXCL, no overwrite


@pytest.mark.parametrize("filename", (
    "../escape.acsm", "danger\\name", "nom français.acsm",
    "hidden..acsm", ".hidden", "a" * 101,
))
def test_invalid_operator_filename_refused_without_write(tmp_path, filename):
    # All paths normalized only after strict basename inspection. Traversal
    # attempts cannot create outside files because the chosen filename is
    # checked against a bound ASCII allowlist.
    target = tmp_path / filename
    before = set(tmp_path.iterdir())
    with pytest.raises(RecorderError, match="download_destination_invalid"):
        saver.save_chosen_download(FakeDownload(), target)
    assert set(tmp_path.iterdir()) == before


def test_destination_symlink_and_untrusted_permissions_refused(tmp_path):
    victim = tmp_path / "victim"
    victim.write_bytes(b"DO_NOT_TOUCH")
    link = tmp_path / "fictional.acsm"
    link.symlink_to(victim)
    with pytest.raises(RecorderError, match="download_save_failed"):
        saver.save_chosen_download(FakeDownload(), link)
    assert victim.read_bytes() == b"DO_NOT_TOUCH"
    insecure = tmp_path / "group-writable"
    insecure.mkdir(mode=0o777)
    insecure.chmod(0o777)
    try:
        with pytest.raises(RecorderError, match="download_destination_invalid"):
            saver.save_chosen_download(
                FakeDownload(), insecure / "operator-chosen.acsm"
            )
        assert list(insecure.iterdir()) == []
    finally:
        insecure.chmod(0o700)


def test_download_failure_or_limit_removes_partial_user_file(tmp_path, monkeypatch):
    target = tmp_path / "only-after-complete.acsm"
    with pytest.raises(RecorderError, match="download_save_failed"):
        saver.save_chosen_download(FakeDownload(fail=True), target)
    assert not target.exists()
    monkeypatch.setattr(saver, "MAX_MANUAL_DOWNLOAD_BYTES", 8)
    with pytest.raises(RecorderError, match="download_limit"):
        saver.save_chosen_download(FakeDownload(b"X" * 32), target)
    assert not target.exists()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("header", (
    {"content-type": "application/vnd.adobe.adept+xml"},
    {"content-type": "application/epub+zip"},
    {"content-type": "application/pdf"},
    {"content-type": "text/xml",
     "content-disposition": 'attachment; filename="fake.acsm"'},
    {"content-type": "application/json",
     "content-disposition": "attachment; filename=fictional.xml"},
))
def test_binary_download_response_never_calls_body_or_enters_trace(header):
    class FakeRequest:
        url = ORIGIN + "/download?BookId=123"
        method = "GET"
        headers = {}
        def sizes(self):
            return {"responseBodySize": 35, "requestBodySize": 0}

    class FakeResponse:
        status = 200
        headers = {**header, "content-length": "35"}
        def __init__(self, request):
            self.request = request
        def body(self):
            raise AssertionError("forbidden download response body read")

    trace = PrivateHttpTrace((ORIGIN,))
    req = FakeRequest()
    trace.request(req)
    trace.response(FakeResponse(req))
    trace.finished(req)
    try:
        assert len(trace.exchanges) == 1
        record = trace.exchanges[0]
        assert record.response_body is None
        assert record.response_body_state == "binary_skipped"
        assert record.mime == ""
        assert trace.omitted["binary_skipped"] == 1
    finally:
        trace.destroy()


def test_ordinary_json_is_not_confused_with_download():
    assert not saver.is_download_response(type("R", (), {
        "headers": {"content-type": "application/json"}
    })())
