from __future__ import annotations

from io import BytesIO
import socket
from types import SimpleNamespace

import pytest

from cifereca.plugin_api import fetch_public_http


PUBLIC = "93.184.216.34"
OTHER_PUBLIC = "1.1.1.1"


def _records(host, port, *, addresses=(PUBLIC,), **_kwargs):
    return [
        (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, port))
        for ip in addresses
    ]


class FakeSocket:
    def __init__(self, wire: bytes, *, peer=PUBLIC):
        self.wire = wire
        self.peer = peer
        self.connected_to = None
        self.closed = False
        self.sent = b""

    def settimeout(self, timeout):
        self.timeout = timeout

    def connect(self, sockaddr):
        self.connected_to = sockaddr

    def getpeername(self):
        return (self.peer, self.connected_to[1] if self.connected_to else 443)

    def sendall(self, payload):
        self.sent += payload

    def makefile(self, _mode):
        return BytesIO(self.wire)

    def close(self):
        self.closed = True


def _http(status=200, body=b"<rss/>", *, headers=None):
    heads = dict(headers or {})
    heads.setdefault("Content-Length", str(len(body)))
    return (
        f"HTTP/1.1 {status} TEST\r\n".encode("ascii")
        + b"".join(f"{key}: {value}\r\n".encode("ascii") for key, value in heads.items())
        + b"\r\n" + body
    )


class Factory:
    def __init__(self, *wires, peer=PUBLIC):
        self.wires = list(wires)
        self.peer = peer
        self.sockets = []

    def __call__(self, *_args):
        sock = FakeSocket(self.wires.pop(0), peer=self.peer)
        self.sockets.append(sock)
        return sock


@pytest.mark.parametrize("ip", [
    "127.0.0.1", "10.0.0.1", "192.168.1.4", "169.254.0.1",
    "0.0.0.0", "100.64.0.1", "224.0.0.1", "::1", "fc00::1",
])
def test_non_global_resolved_ip_is_rejected_before_dial(ip):
    calls = []

    def resolver(_host, port, **_kwargs):
        family = socket.AF_INET6 if ":" in ip else socket.AF_INET
        return [(family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, port))]

    with pytest.raises(ValueError, match="DNS vers une adresse non publique"):
        fetch_public_http(
            "https://news.example/rss", resolver=resolver,
            socket_factory=lambda *_: calls.append("dial"),
        )
    assert calls == []


def test_mixed_dns_answers_fail_closed_before_any_connection():
    calls = []
    with pytest.raises(ValueError, match="non publique"):
        fetch_public_http(
            "https://news.example/rss",
            resolver=lambda h, p, **k: _records(h, p, addresses=(PUBLIC, "10.0.0.1")),
            socket_factory=lambda *_: calls.append("dial"),
        )
    assert calls == []


def test_pinned_socket_uses_validated_ip_without_dns_reresolution():
    dns = []
    factory = Factory(_http(body=b"<rss/>"))

    def resolver(host, port, **_kwargs):
        dns.append((host, port))
        if len(dns) != 1:
            raise AssertionError("DNS was resolved a second time!")
        return _records(host, port)

    final, body = fetch_public_http(
        "http://news.example/feed?q=1", resolver=resolver, socket_factory=factory,
    )
    assert final == "http://news.example/feed?q=1"
    assert body == b"<rss/>"
    assert dns == [("news.example", 80)]
    assert factory.sockets[0].connected_to == (PUBLIC, 80)
    assert b"GET /feed?q=1 HTTP/1.1" in factory.sockets[0].sent
    assert b"Host: news.example" in factory.sockets[0].sent
    assert factory.sockets[0].closed


def test_socket_actual_peer_is_checked_even_when_resolver_says_public():
    factory = Factory(_http(), peer="127.0.0.1")
    with pytest.raises(ValueError, match="Socket connecté à une adresse non publique"):
        fetch_public_http(
            "http://news.example/rss", resolver=_records, socket_factory=factory,
        )
    assert factory.sockets[0].closed
    assert factory.sockets[0].sent == b""


def test_socket_actual_peer_must_match_pinned_ip():
    factory = Factory(_http(), peer=OTHER_PUBLIC)
    with pytest.raises(ValueError, match="Destination socket différente"):
        fetch_public_http(
            "http://news.example/rss", resolver=_records, socket_factory=factory,
        )
    assert factory.sockets[0].sent == b""


def test_https_keeps_hostname_as_tls_sni_and_certificate_name(monkeypatch):
    names = []
    factory = Factory(_http(body=b"<feed/>"))

    class TLSContext:
        check_hostname = True
        verify_mode = "CERT_REQUIRED"

        def wrap_socket(self, sock, *, server_hostname):
            names.append(server_hostname)
            return sock

    monkeypatch.setattr("cifereca.plugin_api.public_http.ssl.create_default_context", TLSContext)
    final, body = fetch_public_http(
        "https://news.example/rss", resolver=_records, socket_factory=factory,
    )
    assert (final, body) == ("https://news.example/rss", b"<feed/>")
    assert names == ["news.example"]
    assert factory.sockets[0].connected_to == (PUBLIC, 443)


def test_redirect_dns_public_then_private_never_dials_second_target():
    visited = []
    factory = Factory(_http(302, b"", headers={"Location": "https://next.example/feed"}))

    def resolver(host, port, **kw):
        visited.append(host)
        return _records(host, port, addresses=(PUBLIC if host == "news.example" else "127.0.0.1",))

    class TLS:
        def wrap_socket(self, sock, *, server_hostname):
            return sock

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("cifereca.plugin_api.public_http.ssl.create_default_context", TLS)
        with pytest.raises(ValueError, match="non publique"):
            fetch_public_http(
                "https://news.example/start", resolver=resolver, socket_factory=factory,
            )
    assert visited == ["news.example", "next.example"]
    assert len(factory.sockets) == 1
    assert factory.sockets[0].closed


def test_redirect_to_localhost_or_literal_private_ip_is_rejected_without_dns():
    for target in ("http://127.0.0.1/admin", "http://localhost/admin"):
        calls = []
        factory = Factory(_http(302, b"", headers={"Location": target}))
        with pytest.raises(ValueError, match="publique"):
            fetch_public_http(
                "http://news.example/rss",
                resolver=lambda h, p, **k: (calls.append(h) or _records(h, p)),
                socket_factory=factory,
            )
        assert calls == ["news.example"]
        assert len(factory.sockets) == 1


def test_response_size_cap_and_content_length_are_checked():
    for wire in (_http(body=b"012345678901234"), _http(headers={"Content-Length": "9999"})):
        factory = Factory(wire)
        with pytest.raises(ValueError, match="volumineuse"):
            fetch_public_http(
                "http://news.example/feed", max_bytes=10,
                resolver=_records, socket_factory=factory,
            )
        assert factory.sockets[0].closed


def test_bad_urls_rejected_without_dns_or_socket_side_effects():
    for url in ("file:///etc/passwd", "http://user:password@news.example",
                "http://[::1]/", "http://127.0.0.1/", "http://a.local/", "ftp://public.example/rss"):
        with pytest.raises(ValueError, match="publique"):
            fetch_public_http(
                url, resolver=lambda *_a, **_k: pytest.fail("DNS used"),
                socket_factory=lambda *_a: pytest.fail("socket used"),
            )
