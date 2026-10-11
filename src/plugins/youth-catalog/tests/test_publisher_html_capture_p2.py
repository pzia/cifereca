"""Offline verification of the explicit publisher HTML capture preflight."""
import importlib.util
from pathlib import Path
import io

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "capture_publisher_html.py"
spec = importlib.util.spec_from_file_location("publisher_fixture_capture", SCRIPT)
capture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(capture)


class Response:
    def __init__(self, *, url, body, status=200, content_type="text/html"):
        self.url = url
        self.data = io.BytesIO(body)
        self.status = status
        self.headers = {"Content-Type": content_type}

    def read(self, size):
        return self.data.read(size)

    def geturl(self):
        return self.url

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.data.close()


def html():
    return b"<!DOCTYPE html><html><head><title>Source</title></head><body>Public catalogue</body></html>"


def test_allowlist_contains_exact_official_pages_no_dynamic_urls():
    assert set(capture.URLS) == {
        "rageot-catalog", "rageot-page-2", "rageot-age-8",
        "rageot-book", "bayard-book",
    }
    assert all(url.startswith("https://www.") and "?" not in url
               for url in capture.URLS.values())


def test_private_capture_is_explicit_and_provenance_verifiable(tmp_path):
    calls = []
    def opener(req):
        calls.append((req.full_url, req.get_method()))
        return Response(url=req.full_url, body=html())
    raw, meta = capture.capture("rageot-catalog", tmp_path, opener=opener)
    assert raw.read_bytes() == html()
    import json
    import hashlib
    data = json.loads(meta.read_text(encoding="utf-8"))
    assert data["source_url"] == capture.URLS["rageot-catalog"]
    assert data["sha256"] == hashlib.sha256(html()).hexdigest()
    assert data["review_required"] is True
    assert raw.stat().st_mode & 0o777 == 0o600
    assert calls == [(capture.URLS["rageot-catalog"], "GET")]
    with pytest.raises(FileExistsError):
        capture.capture("rageot-catalog", tmp_path, opener=opener)


@pytest.mark.parametrize("options", [
    {"status": 403},
    {"status": 301},
    {"content_type": "application/json"},
    {"body": b"<html><title>Just a moment...</title></html>"},
    {"body": b"not a document"},
    {"body": b"<!doctype html><html>" + b"X" * (capture.MAX_BYTES + 1)},
])
def test_bad_response_does_not_write_html(tmp_path, options):
    url = capture.URLS["rageot-book"]
    body = options.pop("body", html())
    with pytest.raises(ValueError):
        capture.capture(
            "rageot-book", tmp_path,
            opener=lambda req: Response(url=url, body=body, **options),
        )
    assert not list(tmp_path.iterdir())


def test_redirect_is_not_mistaken_for_a_product_fixture(tmp_path):
    url = capture.URLS["rageot-book"]
    with pytest.raises(ValueError, match="final URL"):
        capture.capture(
            "rageot-book", tmp_path,
            opener=lambda req: Response(
                url="https://www.rageot.fr/livres-rageot/", body=html(),
            ),
        )
    assert not list(tmp_path.iterdir())


def test_rejects_unknown_fixtures_without_http(tmp_path):
    with pytest.raises(ValueError, match="Unknown"):
        capture.capture(
            "https://internal.invalid/admin", tmp_path,
            opener=lambda _: (_ for _ in ()).throw(AssertionError("network request")),
        )


def test_rejects_unreviewed_html_inside_repo(tmp_path):
    in_repo = SCRIPT.parents[4] / "src" / "plugins" / "youth-catalog" / "tests"
    with pytest.raises(ValueError, match="repository"):
        capture.capture("rageot-catalog", in_repo,
                        opener=lambda _: (_ for _ in ()).throw(AssertionError("network request")))
