"""#2460: HttpTransport opt-in same-origin EPUB redirects and error codes.

The entire transport is tested on httpx.MockTransport. No real network,
account, signed URL, authentication, provider borrowing or DRM.
"""
from __future__ import annotations

import io
import logging
import zipfile

import httpx
import pytest

from cifereca.plugin_api import TemporaryUnavailable, UnsupportedOperation
from cifereca_http_transport import HttpTransport


HOST = "https://opds2.ebooksgratuits.org"
START = HOST + "/index.php/fetch/42.epub"
TARGET = HOST + "/index.php/fetch/42-public.epub"


class Context:
    config = {"timeout_seconds": 4, "max_download_bytes": 1024 * 1024,
              "max_redirects": 4}
    logger = logging.getLogger("test.opds.transport.redirect")


def valid_epub():
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as book:
        book.writestr("mimetype", "application/epub+zip")
        book.writestr(
            "META-INF/container.xml",
            '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            '<rootfiles><rootfile full-path="content.opf" '
            'media-type="application/oebps-package+xml"/></rootfiles></container>',
        )
        book.writestr("content.opf", '<package xmlns="http://www.idpf.org/2007/opf"/>')
    return payload.getvalue()


def request_source():
    return {
        "url": START,
        "redirect_policy": "same_origin",
        "classify_http_status": True,
        "max_redirects": 2,
        "max_bytes": 1024 * 1024,
        "allowed_content_types": ("application/epub+zip",),
        "expected_format": "epub",
        "headers": {"Accept": "application/epub+zip", "Referer": HOST + "/index.php/feed"},
        "overwrite": True,
    }


def transport(handler):
    obj = HttpTransport(Context())
    obj.client.close()
    obj.client = httpx.Client(transport=httpx.MockTransport(handler),
                              follow_redirects=False, trust_env=False)
    return obj


def test_real_epub_package_through_two_same_origin_redirects(tmp_path):
    called = []
    # Compare the exact served bytes, not a newly generated ZIP whose DOS
    # timestamps can differ when the request crosses a two-second boundary.
    served_epub = valid_epub()
    def handler(request):
        called.append(str(request.url))
        if str(request.url) == START:
            return httpx.Response(302, headers={
                "Location": "/index.php/fetch/42-intermediate.epub",
            }, request=request)
        if str(request.url) == HOST + "/index.php/fetch/42-intermediate.epub":
            return httpx.Response(307, headers={"Location": TARGET}, request=request)
        assert str(request.url) == TARGET
        return httpx.Response(
            200, content=served_epub,
            headers={"Content-Type": "application/epub+zip"},
            request=request,
        )
    x = transport(handler)
    try:
        file = tmp_path / "Livre public.epub"
        assert x.fetch(request_source(), file) == file
        assert file.read_bytes() == served_epub
        assert called == [START, HOST + "/index.php/fetch/42-intermediate.epub", TARGET]
    finally:
        x.close()


@pytest.mark.parametrize("target", [
    "https://evil.invalid/index.php/fetch/42.epub",
    "http://opds2.ebooksgratuits.org/index.php/fetch/42.epub",
    "https://127.0.0.1/index.php/fetch/42.epub",
    "//other.invalid/index.php/fetch/42.epub",
])
def test_foreign_or_downgraded_redirect_denied_before_next_get(tmp_path, target):
    received = []
    def handler(request):
        received.append(str(request.url))
        return httpx.Response(302, headers={"Location": target}, request=request)
    t = transport(handler)
    try:
        path = tmp_path / "never.epub"
        with pytest.raises(UnsupportedOperation, match="hors origine") as err:
            t.fetch(request_source(), path)
        assert "evil.invalid" not in str(err.value)
        assert received == [START]
        assert not path.exists()
        assert not list(tmp_path.glob("*.part"))
    finally:
        t.close()


def test_explicit_https_default_port_still_counts_as_same_origin(tmp_path):
    requested = []
    def handler(req):
        requested.append(str(req.url))
        if len(requested) == 1:
            return httpx.Response(
                302,
                headers={"Location": "https://opds2.ebooksgratuits.org:443/index.php/fetch/42-public.epub"},
                request=req,
            )
        return httpx.Response(200, content=valid_epub(),
                              headers={"Content-Type": "application/epub+zip"}, request=req)
    t = transport(handler)
    try:
        assert t.fetch(request_source(), tmp_path / "valid.epub").is_file()
        assert len(requested) == 2
    finally:
        t.close()


@pytest.mark.parametrize("status,expected", [
    (401, "auth_required"),
    (403, "access_denied"),
    (404, "not_found"),
    (410, "not_found"),
    (429, "rate_limited"),
    (503, "service_unavailable"),
])
def test_opt_in_http_error_classification_no_raw_body(tmp_path,status,expected):
    calls = []
    def handler(req):
        calls.append(str(req.url))
        return httpx.Response(
            status,
            content=b"PRIVATE_URL https://evil.invalid/?token=PRIVATE",
            request=req,
        )
    t = transport(handler)
    try:
        target=tmp_path / "never.epub"
        with pytest.raises(TemporaryUnavailable) as caught:
            t.fetch(request_source(), target)
        assert str(caught.value) == "transport.http: " + expected
        assert "PRIVATE" not in str(caught.value)
        assert calls == [START]
        assert not target.exists()
    finally:
        t.close()


def test_broken_epub_after_permitted_redirect_fails_before_publish(tmp_path):
    visited = []
    def handler(req):
        visited.append(req.url.path)
        if len(visited) == 1:
            return httpx.Response(302, headers={
                "Location": "/index.php/fetch/42-broken.epub",
            }, request=req)
        return httpx.Response(200, content=b"<html>not an EPUB</html>",
                              headers={"Content-Type": "text/html"}, request=req)
    t = transport(handler)
    try:
        target=tmp_path / "broken.epub"
        with pytest.raises(UnsupportedOperation, match="HTML"):
            t.fetch(request_source(),target)
        assert not target.exists()
        assert len(visited)==2
    finally:
        t.close()


@pytest.mark.parametrize("bad_policy", ["anywhere", None, 4, True])
def test_bad_redirect_policy_rejected_before_http(tmp_path,bad_policy):
    def handler(req):
        pytest.fail("invalid redirect policy must not make HTTP")
    t=transport(handler)
    try:
        source={**request_source(),"redirect_policy":bad_policy}
        with pytest.raises(UnsupportedOperation, match="politique de redirection invalide"):
            t.fetch(source,tmp_path / "none.epub")
        assert not list(tmp_path.iterdir())
    finally:
        t.close()
