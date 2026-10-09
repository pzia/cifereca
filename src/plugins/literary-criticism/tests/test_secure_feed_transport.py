"""No real network: exercise the pinned TCP destination and redirect policy."""
from __future__ import annotations

import socket
import ssl

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


@pytest.mark.parametrize("blocked", [
    "127.0.0.1", "10.0.0.1", "172.16.0.1", "192.168.3.2",
    "169.254.169.254", "::1", "fd00::1", "fe80::1", "0.0.0.0",
])
def test_dns_private_targets_fail_before_a_socket_is_created(blocked):
    made = []
    with pytest.raises(ValueError, match="non publique"):
        feeds._public_socket(
            "news.example", 443, timeout=1,
            resolve=lambda *_args, **_kwargs: [_answer(blocked)],
            socket_factory=lambda *_args: made.append(True),
        )
    assert made == []


def test_mixed_public_private_dns_answers_fail_closed():
    created = []
    with pytest.raises(ValueError, match="non publique"):
        feeds._public_socket(
            "news.example", 443, timeout=1,
            resolve=lambda *_args, **_kwargs: [_answer(PUBLIC_V4), _answer("10.1.2.3")],
            socket_factory=lambda *_args: created.append(True),
        )
    assert created == []


@pytest.mark.parametrize("ip", [PUBLIC_V4, PUBLIC_V6])
def test_global_dns_address_connects_using_numeric_sockaddr_only(ip):
    calls, addresses, created = [], [], []
    def resolve(host, port, **kwargs):
        addresses.append((host, port))
        return [_answer(ip)]
    def make_socket(*args):
        sock = FakeSocket(ip, calls)
        created.append(sock)
        return sock
    sock = feeds._public_socket(
        "news.example", 443, timeout=1.5,
        resolve=resolve, socket_factory=make_socket,
    )
    assert sock is created[0]
    assert addresses == [("news.example", 443)]
    assert ("connect", _answer(ip)[4]) in calls
    assert all("news.example" not in str(call) for call in calls)


def test_dns_rebinding_cannot_change_the_actual_peer(monkeypatch):
    # A peer address different from the vetted IP is never returned to HTTP.
    sock = FakeSocket("127.0.0.1", [])
    with pytest.raises(ValueError, match="Socket connecté"):
        feeds._public_socket(
            "news.example", 443, timeout=1,
            resolve=lambda *_args, **_kwargs: [_answer(PUBLIC_V4)],
            socket_factory=lambda *_args: sock,
        )
    assert sock.closed


def test_https_preserves_original_name_for_sni_and_certificate(monkeypatch):
    calls = []
    sock = FakeSocket(PUBLIC_V4, calls)
    monkeypatch.setattr(feeds, "_public_socket", lambda host, port, **kwargs: (
        calls.append(("dial", host, port)) or sock
    ))
    class Context:
        verify_mode = ssl.CERT_REQUIRED
        check_hostname = True

        def wrap_socket(self, transport, *, server_hostname):
            calls.append(("tls", transport, server_hostname))
            return transport
    conn = feeds._PinnedHTTPSConnection(
        "news.example", 443, timeout=2, context=Context(),
    )
    conn.connect()
    assert calls == [("dial", "news.example", 443), ("tls", sock, "news.example")]
    assert ssl.create_default_context().verify_mode == ssl.CERT_REQUIRED


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
