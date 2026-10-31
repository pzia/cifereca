"""#3867: public HTTPS delivery decisions are independent of provider domains.

Network is fully mocked at Core's public_client boundary. Peer DNS pinning
itself has independent transport tests (test_public_httpx_transport.py).
"""
from __future__ import annotations

import os
from pathlib import Path

import httpx
import pytest

from cifereca.core import attested_https_delivery as delivery


FIRST = "https://books.provider.example/get?signature=synthetic-opaque"
CDN = "https://unlisted-cdn.example/file.epub?token=synthetic-another"


def _client(monkeypatch, handler):
    calls = []

    def factory(*, producer, timeout):
        assert producer == "core.selected.delivery"
        assert timeout == 5
        calls.append(1)
        return httpx.Client(
            transport=httpx.MockTransport(handler),
            follow_redirects=False, trust_env=False,
        )

    monkeypatch.setattr(delivery.HTTPX_TRANSPORT, "public_client", factory)
    return calls


def test_unlisted_https_origin_is_downloaded_and_sensitive_headers_do_not_cross(
    tmp_path, monkeypatch,
):
    seen = []

    def handler(request):
        seen.append(request)
        if str(request.url) == FIRST:
            return httpx.Response(302, headers={
                "Location": CDN, "Set-Cookie": "session=private; Path=/",
            })
        assert str(request.url) == CDN
        return httpx.Response(200, content=b"synthetic epub", headers={
            "Content-Type": "application/epub+zip",
        })

    calls = _client(monkeypatch, handler)
    target = tmp_path / "received.epub"
    result = delivery.download_attested_https(
        producer="core.selected.delivery", url=FIRST, target=target,
        headers={
            "Authorization": "Bearer opaque-first-origin",
            "Cookie": "account=private",
            "Referer": "https://books.provider.example/private",
            "X-CSRF-Token": "private",
            "Accept": "application/epub+zip",
        }, max_bytes=1024, timeout=5, admission_current=lambda: True,
    )
    assert calls == [1]
    assert result.path == target and result.size == len(b"synthetic epub")
    assert result.redirects == 1 and result.content_type == "application/epub+zip"
    assert target.read_bytes() == b"synthetic epub"
    assert seen[0].headers["authorization"] == "Bearer opaque-first-origin"
    assert seen[0].headers["cookie"] == "account=private"
    for private in ("authorization", "cookie", "referer", "x-csrf-token"):
        assert private not in seen[1].headers
    assert seen[1].headers["accept"] == "application/epub+zip"
    assert not list(tmp_path.glob(".delivery-*"))


@pytest.mark.parametrize("location", [
    "http://unlisted-cdn.example/file.epub",
    "https://127.0.0.1/private", "https://[::1]/private",
    "file:///etc/passwd", "javascript:alert(1)",
    "https://user:pass@unlisted-cdn.example/file.epub",
    "https://unlisted-cdn.example/file.epub#fragment",
    "https://unlisted-cdn.example/\\evil",
])
def test_invalid_location_never_creates_second_request_or_file(
    tmp_path, monkeypatch, location,
):
    visited = []

    def handler(request):
        visited.append(str(request.url))
        return httpx.Response(302, headers={"location": location})

    _client(monkeypatch, handler)
    target = tmp_path / "rejected.epub"
    with pytest.raises(ValueError):
        delivery.download_attested_https(
            producer="core.selected.delivery", url=FIRST, target=target,
            timeout=5, admission_current=lambda: True,
        )
    assert visited == [FIRST]
    assert not target.exists()


def test_redirect_cycle_and_limit_are_bounded_without_writing(tmp_path, monkeypatch):
    seen = []

    def handler(request):
        seen.append(str(request.url))
        if str(request.url) == FIRST:
            return httpx.Response(307, headers={"location": CDN})
        return httpx.Response(308, headers={"location": FIRST})

    _client(monkeypatch, handler)
    with pytest.raises(ValueError, match="delivery_redirect_loop"):
        delivery.download_attested_https(
            producer="core.selected.delivery", url=FIRST,
            target=tmp_path / "none.epub", timeout=5, admission_current=lambda: True,
        )
    assert seen == [FIRST, CDN]
    with pytest.raises(ValueError, match="delivery_redirect_limit"):
        delivery.download_attested_https(
            producer="core.selected.delivery", url=FIRST,
            target=tmp_path / "none.epub", max_redirects=0, timeout=5, admission_current=lambda: True,
        )
    assert not (tmp_path / "none.epub").exists()


@pytest.mark.parametrize("scenario", ["length", "stream", "html", "empty", "error"])
def test_failed_delivery_never_creates_final_file_or_leaves_partial(
    tmp_path, monkeypatch, scenario,
):
    def handler(request):
        if scenario == "length":
            return httpx.Response(200, content=b"x", headers={"content-length": "999"})
        if scenario == "stream":
            return httpx.Response(200, content=b"01234567890123456789")
        if scenario == "html":
            return httpx.Response(200, content=b"<html/>", headers={"content-type": "text/html"})
        if scenario == "empty":
            return httpx.Response(200, content=b"")
        return httpx.Response(500, content=b"error")

    _client(monkeypatch, handler)
    target = tmp_path / "received.epub"
    with pytest.raises(ValueError):
        delivery.download_attested_https(
            producer="core.selected.delivery", url=FIRST, target=target,
            max_bytes=10, timeout=5, admission_current=lambda: True,
        )
    assert not target.exists()
    assert list(tmp_path.iterdir()) == []


def test_symlink_destination_and_symlinked_parent_are_not_writable(
    tmp_path, monkeypatch,
):
    external = tmp_path / "external"
    external.mkdir()
    outside_file = external / "real.epub"
    outside_file.write_bytes(b"unchanged")

    _client(monkeypatch, lambda request: httpx.Response(200, content=b"new content"))
    parent = tmp_path / "staging"
    parent.mkdir()
    target = parent / "received.epub"
    target.symlink_to(outside_file)
    with pytest.raises(ValueError, match="delivery_target_exists"):
        delivery.download_attested_https(
            producer="core.selected.delivery", url=FIRST,
            target=target, timeout=5, admission_current=lambda: True,
        )
    assert outside_file.read_bytes() == b"unchanged"
    target.unlink()

    linked = tmp_path / "linked"
    linked.symlink_to(external, target_is_directory=True)
    with pytest.raises(ValueError, match="delivery_staging_unsafe"):
        delivery.download_attested_https(
            producer="core.selected.delivery", url=FIRST,
            target=linked / "received.epub", timeout=5, admission_current=lambda: True,
        )
    assert list(external.iterdir()) == [outside_file]


def test_existing_target_is_never_replaced(tmp_path, monkeypatch):
    target = tmp_path / "received.epub"
    target.write_bytes(b"existing proof")
    calls = []
    _client(monkeypatch, lambda request: calls.append(1) or httpx.Response(200, content=b"new"))
    with pytest.raises(ValueError, match="delivery_target_exists"):
        delivery.download_attested_https(
            producer="core.selected.delivery", url=FIRST,
            target=target, timeout=5, admission_current=lambda: True,
        )
    assert target.read_bytes() == b"existing proof"
    assert calls == []


@pytest.mark.parametrize("invalid", [
    "http://books.provider.example/file",
    "https://127.0.0.1/file",
    "https://[2606:4700:4700::1111]/file",
    "https://user@books.provider.example/file",
    "https://books.provider.example/file#fragment",
    "https://books.provider.example/%ZZ\\bad",
])
def test_untrusted_initial_url_denied_before_network(
    tmp_path, monkeypatch, invalid,
):
    calls = []
    _client(monkeypatch, lambda request: calls.append(1) or httpx.Response(200, content=b"x"))
    with pytest.raises(ValueError):
        delivery.download_attested_https(
            producer="core.selected.delivery", url=invalid,
            target=tmp_path / "bad.epub", timeout=5, admission_current=lambda: True,
        )
    assert calls == []
    assert not (tmp_path / "bad.epub").exists()


def test_admission_revoked_after_redirect_denies_followup_before_get(
    tmp_path, monkeypatch,
):
    visited = []
    checks = []

    def handler(request):
        visited.append(str(request.url))
        return httpx.Response(302, headers={"location": CDN})

    def current():
        checks.append(1)
        return len(checks) <= 2

    _client(monkeypatch, handler)
    with pytest.raises(ValueError, match="delivery_admission_changed"):
        delivery.download_attested_https(
            producer="core.selected.delivery", url=FIRST,
            target=tmp_path / "unauthorized.epub",
            admission_current=current, timeout=5,
        )
    assert visited == [FIRST]
    assert not (tmp_path / "unauthorized.epub").exists()


def test_rejected_final_publish_after_stream_preserves_no_target(
    tmp_path, monkeypatch,
):
    checks = []

    def current():
        checks.append(1)
        # Preflight, first hop and the data chunk are admitted; the final
        # byte-to-path transition must independently recheck consent.
        return len(checks) <= 3

    _client(monkeypatch, lambda request: httpx.Response(200, content=b"valid epub bytes"))
    with pytest.raises(ValueError, match="delivery_admission_changed"):
        delivery.download_attested_https(
            producer="core.selected.delivery", url=FIRST,
            target=tmp_path / "no-publication.epub",
            admission_current=current, timeout=5,
        )
    assert len(checks) == 4
    assert list(tmp_path.iterdir()) == []


def test_transport_service_exposes_same_bounded_core_download(
    tmp_path, monkeypatch,
):
    from cifereca.plugin_api import HTTPX_TRANSPORT

    _client(monkeypatch, lambda request: httpx.Response(
        200, content=b"verified candidate",
        headers={"Content-Type": "application/epub+zip"},
    ))
    result = HTTPX_TRANSPORT.attested_https_download(
        producer="core.selected.delivery", url=FIRST,
        target=tmp_path / "from-service.epub",
        admission_current=lambda: True, timeout=5,
        max_bytes=100, max_redirects=2,
    )
    assert result.sha256 == delivery.hashlib.sha256(b"verified candidate").hexdigest()
    assert result.path.read_bytes() == b"verified candidate"


def test_httpx_error_cannot_leak_signed_url_or_transport_diagnostic(
    tmp_path, monkeypatch,
):
    def handler(request):
        raise httpx.ConnectError(
            "private-signed-url-and-account=DO-NOT-LOG", request=request,
        )

    _client(monkeypatch, handler)
    with pytest.raises(ValueError, match="^delivery_transport_unavailable$") as err:
        delivery.download_attested_https(
            producer="core.selected.delivery", url=FIRST,
            target=tmp_path / "failed.epub",
            admission_current=lambda: True, timeout=5,
        )
    assert "DO-NOT-LOG" not in str(err.value)
    assert not list(tmp_path.iterdir())
