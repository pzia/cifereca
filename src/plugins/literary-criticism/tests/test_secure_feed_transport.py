"""No real network: exercise the pinned TCP destination and redirect policy."""
from __future__ import annotations

import socket

import pytest

import criticism_feeds as feeds


PUBLIC_V4 = "93.184.216.34"
PUBLIC_V6 = "2606:4700:4700::1111"


def _answer(ip):
    if ":" in ip:
        return (socket.AF_INET6, socket.SOCK_STREAM, 6, "", (ip, 443, 0, 0))
    return (socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443))


class FakeSocket:
    def __init__(self, peer, calls):
        self.peer, self.calls, self.closed = peer, calls, False

    def settimeout(self, timeout):
        self.calls.append(("timeout", timeout))

    def connect(self, sockaddr):
        self.calls.append(("connect", sockaddr))

    def getpeername(self):
        return (self.peer, 443)

    def close(self):
        self.closed = True


# Socket/DNS/TLS unit assertions moved to the Core transport test suite.
# Keep the installed FeedSource tests below: they prove the default plugin
# path still enforces that policy, not only an injected test HTTP client.


def test_redirect_to_private_host_never_attempts_second_connection(monkeypatch):
    requests = []
    def fake(url, *, timeout):
        requests.append(url)
        return (302, "http://127.0.0.1/internal", b"")
    monkeypatch.setattr(feeds, "_fetch_pinned", fake)
    with pytest.raises(ValueError, match="non publique"):
        feeds.fetch_public("https://news.example/feed")
    assert requests == ["https://news.example/feed"]


def test_dns_is_checked_again_for_each_public_redirect(monkeypatch):
    requests = []
    def fake(url, *, timeout):
        requests.append(url)
        if len(requests) == 1:
            return (302, "https://news2.example/rss", b"")
        return (200, "", b"rss")
    monkeypatch.setattr(feeds, "_fetch_pinned", fake)
    assert feeds.fetch_public("https://news.example/feed") == (
        "https://news2.example/rss", b"rss",
    )
    assert requests == [
        "https://news.example/feed", "https://news2.example/rss",
    ]


def test_https_downgrade_redirect_is_refused(monkeypatch):
    requests = []
    def fake(url, *, timeout):
        requests.append(url)
        return (302, "http://news2.example/rss", b"")
    monkeypatch.setattr(feeds, "_fetch_pinned", fake)
    with pytest.raises(ValueError, match="HTTPS vers HTTP"):
        feeds.fetch_public("https://news.example/feed")
    assert requests == ["https://news.example/feed"]



def test_installed_feed_source_default_fetch_rejects_private_dns_before_socket(monkeypatch):
    created = []

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port, **kwargs: [_answer("10.10.10.10")],
    )
    monkeypatch.setattr(
        socket,
        "socket",
        lambda *args: created.append(args) or FakeSocket("10.10.10.10", []),
    )

    source = feeds.FeedSource("https://news.example/feed")
    assert source.fetch is feeds.fetch_public
    with pytest.raises(ValueError, match="non publique"):
        source.collect()

    # The production FeedSource path rejects the DNS answer before creating TCP.
    assert created == []


def test_installed_feed_source_default_fetch_rejects_unexpected_peer(monkeypatch):
    created = []
    sock = FakeSocket("127.0.0.1", [])

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port, **kwargs: [_answer(PUBLIC_V4)],
    )
    monkeypatch.setattr(
        socket,
        "socket",
        lambda *args: created.append(args) or sock,
    )

    source = feeds.FeedSource("https://news.example/feed")
    assert source.fetch is feeds.fetch_public
    with pytest.raises(ValueError, match="Socket connecté"):
        source.collect()

    assert created
    assert sock.closed
