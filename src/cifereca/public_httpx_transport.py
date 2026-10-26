"""Core HTTPX transport that pins a validated public peer before HTTP/TLS.

HTTPX 0.28.x / HTTPcore 1.0.x only. No proxy or second hostname lookup.
"""
from __future__ import annotations

import ipaddress
import socket
import ssl

import httpcore
import httpx
from httpcore._backends.sync import SyncStream


def _public_socket(
    host, port, *, timeout, resolve=None, socket_factory=None,
):
    """Pin the actual peer IP before sending any HTTP bytes.

    All DNS answers (including resolved CNAME targets) must be global. The
    selected numeric sockaddr goes straight to socket.connect(): no second
    hostname lookup by the HTTP client or a system proxy is possible.
    """
    resolve = resolve or socket.getaddrinfo
    socket_factory = socket_factory or socket.socket
    try:
        answers = resolve(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise ValueError("Résolution DNS du flux impossible") from exc
    if not answers:
        raise ValueError("Aucune adresse DNS pour le flux")
    for family, kind, protocol, _name, address in answers:
        try:
            permitted = (
                family in (socket.AF_INET, socket.AF_INET6)
                and kind == socket.SOCK_STREAM
                and ipaddress.ip_address(address[0]).is_global
            )
        except (ValueError, IndexError, TypeError):
            permitted = False
        if not permitted:
            raise ValueError("Résolution DNS vers une adresse non publique refusée")
    last_error = None
    for family, kind, protocol, _name, address in answers:
        conn = socket_factory(family, kind, protocol)
        try:
            conn.settimeout(timeout)
            conn.connect(address)
            peer_ip = ipaddress.ip_address(conn.getpeername()[0])
            expected_ip = ipaddress.ip_address(address[0])
            if not peer_ip.is_global or peer_ip != expected_ip:
                raise ValueError("Socket connecté à une adresse non publique ou inattendue")
            return conn
        except ValueError:
            conn.close()
            raise
        except OSError as exc:
            last_error = exc
            conn.close()
    raise ValueError("Connexion au flux public impossible") from last_error


class _PublicNetworkBackend(httpcore.SyncBackend):
    def __init__(self, *, resolve=None, socket_factory=None):
        self.resolve = resolve
        self.socket_factory = socket_factory

    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        if local_address is not None:
            raise ValueError("Public transport does not accept an alternate local binding")
        sock = _public_socket(host, port, timeout=timeout,
                              resolve=self.resolve, socket_factory=self.socket_factory)
        try:
            for option in socket_options or ():
                sock.setsockopt(*option)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            # HTTPcore retains the original origin for Host, SNI and TLS
            # certificate verification; only the TCP destination is pinned.
            return SyncStream(sock)
        except BaseException:
            sock.close()
            raise


class PublicOnlyHTTPTransport(httpx.HTTPTransport):
    def __init__(self):
        # Match the former public feed transport's system certificate trust,
        # not a per-plugin or environment-selected proxy/certificate context.
        super().__init__(verify=ssl.create_default_context(), trust_env=False,
                         http1=True, http2=False, retries=0,
                         limits=httpx.Limits(max_connections=1, max_keepalive_connections=0))
        # Version-bounded instance configuration, no global monkeypatch.
        # HTTPX has no public network_backend argument; HTTPcore owns TCP/TLS.
        self._pool._network_backend = _PublicNetworkBackend()
