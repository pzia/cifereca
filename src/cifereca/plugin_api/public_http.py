"""HTTP(S) fetch for public feeds with DNS-pinned, non-private connections.

Unlike URL-only/lookup-only SSRF checks, this module validates *every* DNS
answer, connects to the validated numeric sockaddr without re-resolving the
hostname, and checks the actual socket peer before sending the HTTP request.
For TLS the original hostname remains the SNI and certificate-verification name.
No proxy or ambient credentials are consulted. Do not use this for loopback
simulation endpoints: inject an explicitly test-only client instead.
"""
from __future__ import annotations

import http.client
import ipaddress
import socket
import ssl
from urllib.parse import urljoin, urlsplit, urlunsplit

_MAX_BYTES = 2 * 1024 * 1024
_MAX_REDIRECTS = 4
_MAX_TIMEOUT = 15.0
_REDIRECTS = frozenset((301, 302, 303, 307, 308))
_LOCAL_SUFFIXES = (".localhost", ".local", ".localdomain", ".internal", ".home.arpa")


def _http_url(value: str) -> str:
    try:
        url = urlsplit(str(value or "").strip())
        hostname = (url.hostname or "").rstrip(".").casefold()
        port = url.port
    except ValueError as exc:
        raise ValueError("URL HTTP(S) publique invalide") from exc
    if (url.scheme.casefold() not in {"http", "https"} or not hostname
            or url.username is not None or url.password is not None
            or hostname in {"localhost", "local", "localdomain", "internal"}
            or any(hostname.endswith(suffix) for suffix in _LOCAL_SUFFIXES)
            or "%" in hostname):
        raise ValueError("URL HTTP(S) publique requise")
    try:
        ip = ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        if not ip.is_global:
            raise ValueError("Adresse IP non publique refusée")
    host = f"[{hostname}]" if ":" in hostname else hostname
    default = (url.scheme.casefold() == "https" and port == 443) or (
        url.scheme.casefold() == "http" and port == 80)
    authority = host + (f":{port}" if port and not default else "")
    return urlunsplit((url.scheme.casefold(), authority, url.path or "/", url.query, ""))


def _public_endpoints(host: str, port: int, resolver) -> tuple[tuple, ...]:
    """Reject the entire DNS answer set if even one address is non-global."""
    try:
        candidates = resolver(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise ValueError("Résolution DNS du flux impossible") from exc
    addresses = []
    for family, socktype, protocol, _name, sockaddr in candidates:
        if family not in (socket.AF_INET, socket.AF_INET6) or socktype != socket.SOCK_STREAM:
            raise ValueError("Réponse DNS non prise en charge")
        try:
            address = ipaddress.ip_address(sockaddr[0])
        except ValueError as exc:
            raise ValueError("Réponse DNS invalide") from exc
        if not address.is_global:
            raise ValueError("DNS vers une adresse non publique refusé")
        addresses.append((family, socktype, protocol, sockaddr))
    if not addresses:
        raise ValueError("Aucune destination DNS publique")
    return tuple(addresses)


def _dial(endpoints: tuple[tuple, ...], timeout: float, socket_factory):
    last_error: OSError | None = None
    for family, socktype, protocol, sockaddr in endpoints:
        sock = socket_factory(family, socktype, protocol)
        try:
            sock.settimeout(timeout)
            # sockaddr was validated; no hostname is passed to connect().
            sock.connect(sockaddr)
            peer = ipaddress.ip_address(sock.getpeername()[0])
            if not peer.is_global:
                raise ValueError("Socket connecté à une adresse non publique")
            # Reject transports which unexpectedly connected to a *different*
            # public peer as well (e.g. a test or proxy-like socket factory).
            if peer != ipaddress.ip_address(sockaddr[0]):
                raise ValueError("Destination socket différente de la résolution validée")
            return sock
        except (OSError, ValueError) as exc:
            sock.close()
            if isinstance(exc, ValueError):
                raise
            last_error = exc
    raise OSError("Connexion au flux impossible") from last_error


class _PinnedHTTP(http.client.HTTPConnection):
    def __init__(self, host, port, *, endpoints, timeout, socket_factory):
        super().__init__(host, port, timeout=timeout)
        self._public_endpoints = endpoints
        self._socket_factory = socket_factory

    def connect(self):
        self.sock = _dial(self._public_endpoints, self.timeout, self._socket_factory)


class _PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host, port, *, endpoints, timeout, socket_factory):
        super().__init__(host, port, timeout=timeout, context=ssl.create_default_context())
        self._public_endpoints = endpoints
        self._socket_factory = socket_factory

    def connect(self):
        sock = _dial(self._public_endpoints, self.timeout, self._socket_factory)
        try:
            # Keep DNS hostname for both TLS SNI and certificate validation.
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
        except Exception:
            sock.close()
            raise


def fetch_public_http(
    url: str, *,
    max_bytes: int = _MAX_BYTES,
    timeout: float = _MAX_TIMEOUT,
    max_redirects: int = _MAX_REDIRECTS,
    resolver=None,
    socket_factory=None,
) -> tuple[str, bytes]:
    """GET a small public HTTP(S) document; redirects are checked independently.

    Each hop gets a single DNS resolution, pinning the actual TCP destination.
    A mixed public/private DNS answer fails closed; no fallback to unsafe peers.
    resolver/socket_factory are for deterministic tests, not production callers.
    """
    limit = max(1, min(_MAX_BYTES, int(max_bytes)))
    seconds = max(0.1, min(_MAX_TIMEOUT, float(timeout)))
    hops = max(0, min(_MAX_REDIRECTS, int(max_redirects)))
    lookup = resolver if resolver is not None else socket.getaddrinfo
    factory = socket_factory if socket_factory is not None else socket.socket
    current = _http_url(url)
    for step in range(hops + 1):
        parsed = urlsplit(current)
        host = parsed.hostname or ""
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        endpoints = _public_endpoints(host, port, lookup)
        connection_type = _PinnedHTTPS if parsed.scheme == "https" else _PinnedHTTP
        connection = connection_type(
            host, port, endpoints=endpoints, timeout=seconds, socket_factory=factory,
        )
        try:
            path = urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
            connection.request(
                "GET", path,
                headers={
                    "Accept": "application/atom+xml, application/rss+xml, application/feed+json, application/json, text/html",
                    "Accept-Encoding": "identity",
                    "User-Agent": "cifereca-public-feed/1.0",
                },
            )
            response = connection.getresponse()
            if response.status in _REDIRECTS:
                destination = response.getheader("Location")
                if not destination:
                    raise ValueError("Redirection sans destination")
                if step >= hops:
                    raise ValueError("Trop de redirections")
                target = _http_url(urljoin(current, destination))
                if parsed.scheme == "https" and urlsplit(target).scheme != "https":
                    raise ValueError("Redirection HTTPS vers HTTP refusée")
                current = target
                continue
            if response.status < 200 or response.status >= 300:
                raise ValueError(f"Réponse HTTP non disponible ({response.status})")
            if (response.getheader("Content-Encoding") or "identity").casefold() not in ("identity", ""):
                raise ValueError("Compression HTTP non prise en charge")
            declared = response.getheader("Content-Length", "")
            if declared.isdecimal() and int(declared) > limit:
                raise ValueError("Réponse trop volumineuse")
            chunks, size = [], 0
            while True:
                chunk = response.read(min(65536, limit - size + 1))
                if not chunk:
                    return current, b"".join(chunks)
                size += len(chunk)
                if size > limit:
                    raise ValueError("Réponse trop volumineuse")
                chunks.append(chunk)
        finally:
            connection.close()
    raise ValueError("Trop de redirections")


__all__ = ["fetch_public_http"]
