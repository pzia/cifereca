"""Bounded public RSS/Atom source; no article body fetch or implicit discovery."""
from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from hashlib import sha256
import ipaddress
import http.client
import socket
import ssl
from time import monotonic
from urllib.parse import urljoin, urlsplit
from xml.etree import ElementTree as ET
import json

from literary_criticism import canonical_url, discover_feed_links

MAX_BYTES = 2 * 1024 * 1024
_PRIVATE_HOST_NAMES = {"localhost", "local", "localdomain"}
_PRIVATE_HOST_SUFFIXES = (".localhost", ".local", ".localdomain")


def public_url(value):
    url = canonical_url(value)
    if not url:
        return ""
    host = (urlsplit(url).hostname or "").rstrip(".").casefold()
    if not host or host in _PRIVATE_HOST_NAMES or any(host.endswith(suffix) for suffix in _PRIVATE_HOST_SUFFIXES):
        return ""
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return url
    return url if address.is_global else ""


def public_join(base_url, value):
    raw = str(value or "").strip()
    if not raw:
        return ""
    return public_url(urljoin(base_url, raw))


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


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def connect(self):
        self.sock = _public_socket(self.host, self.port, timeout=self.timeout)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def connect(self):
        sock = _public_socket(self.host, self.port, timeout=self.timeout)
        try:
            # Keep the original hostname for both SNI and certificate checks.
            # Only the TCP destination is the validated numeric address.
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
        except Exception:
            sock.close()
            raise


def _fetch_pinned(url, *, timeout):
    parts = urlsplit(url)
    if parts.scheme == "https":
        connection = _PinnedHTTPSConnection(
            parts.hostname, parts.port or 443, timeout=timeout,
            context=ssl.create_default_context(),
        )
    else:
        connection = _PinnedHTTPConnection(parts.hostname, parts.port or 80, timeout=timeout)
    target = parts.path or "/"
    if parts.query:
        target += "?" + parts.query
    try:
        connection.request(
            "GET", target,
            headers={
                "Accept": "application/atom+xml, application/rss+xml, application/feed+json, application/json, text/html",
                "Accept-Encoding": "identity",
                "Connection": "close",
            },
        )
        response = connection.getresponse()
        if response.status in (301, 302, 303, 307, 308):
            return response.status, response.getheader("Location", ""), b""
        if not 200 <= response.status < 300:
            raise ValueError(f"Flux HTTP : réponse {response.status}")
        payload = response.read(MAX_BYTES + 1)
        if len(payload) > MAX_BYTES:
            raise ValueError("Réponse trop volumineuse")
        return response.status, "", payload
    finally:
        connection.close()


def fetch_public(url, *, client=None):
    """Fetch public feeds without DNS rebinding or private peer access.

    The optional HTTP client is for deterministic test injection only. The
    production path pins every public DNS resolution at TCP connect and does
    TLS hostname verification against the original URL on each redirect.
    """
    deadline = monotonic() + 15.0
    for _ in range(5):
        url = public_url(url)
        if not url:
            raise ValueError("URL HTTP(S) publique requise")
        if client is not None:
            # Explicit injection supports existing offline httpx.MockTransport
            # contract tests; it is never used by the installed FeedSource.
            with client.stream(
                "GET", url,
                headers={"Accept": "application/atom+xml, application/rss+xml, application/feed+json, application/json, text/html"},
            ) as response:
                if response.is_redirect:
                    location = response.headers.get("location", "")
                    if not location:
                        raise ValueError("Redirection sans destination")
                    redirected = public_url(urljoin(url, location))
                    if not redirected:
                        raise ValueError("Redirection vers une URL non publique refusée")
                    url = redirected
                    continue
                response.raise_for_status()
                chunks, size = [], 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > MAX_BYTES:
                        raise ValueError("Réponse trop volumineuse")
                    chunks.append(chunk)
                return url, b"".join(chunks)

        remaining = deadline - monotonic()
        if remaining <= 0:
            raise ValueError("Délai de lecture du flux dépassé")
        status, location, body = _fetch_pinned(url, timeout=remaining)
        if status in (301, 302, 303, 307, 308):
            if not location:
                raise ValueError("Redirection sans destination")
            target = public_url(urljoin(url, location))
            if not target:
                raise ValueError("Redirection vers une URL non publique refusée")
            if urlsplit(url).scheme == "https" and urlsplit(target).scheme != "https":
                raise ValueError("Redirection HTTPS vers HTTP refusée")
            url = target
            continue
        return url, body
    raise ValueError("Trop de redirections")


def date_text(raw):
    raw = str(raw or '').strip()
    if not raw:
        return ''
    try:
        value = datetime.fromisoformat(raw.replace('Z', '+00:00'))
    except ValueError:
        try:
            value = parsedate_to_datetime(raw)
        except (ValueError, TypeError):
            return ''
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def parse_feed(payload, url, *, limit=100):
    if len(payload) > MAX_BYTES:
        raise ValueError('Flux trop volumineux')
    if payload.lstrip().startswith((b'{', b'[')):
        raw = json.loads(payload)
        if not isinstance(raw, dict):
            raise ValueError('Objet JSON Feed attendu')
        if not str(raw.get('version', '')).startswith('https://jsonfeed.org/version/'):
            raise ValueError('JSON Feed attendu')
        items = raw.get('items', [])
        if not isinstance(items, list):
            raise ValueError('Liste items JSON Feed attendue')
        bounded = max(1, min(int(limit), 500))
        return [{'title': r.get('title'), 'url': public_join(url, r.get('url') or r.get('external_url')),
                 'critic_author': ', '.join(a.get('name', '') for a in r.get('authors', []) if isinstance(a, dict)),
                 'published_at': date_text(r.get('date_published')), 'excerpt': r.get('summary', ''),
                 'language': r.get('language') or raw.get('language', ''), 'book': r.get('_cifereca_book', {})}
                for r in items[:bounded] if isinstance(r, dict)]
    # Refuse DTDs/entities before ElementTree; no external or expansive XML.
    if b'<!DOCTYPE' in payload.upper() or b'<!ENTITY' in payload.upper():
        raise ValueError('DTD et entités non autorisées')
    root = ET.fromstring(payload)
    atom = '{http://www.w3.org/2005/Atom}'
    dc = '{http://purl.org/dc/elements/1.1/}'
    bookns = '{https://cifereca.org/ns/book/1}'
    if root.tag not in ('rss', atom + 'feed'):
        raise ValueError('Flux RSS 2 ou Atom attendu')
    channel = root.find('channel') if root.tag == 'rss' else root
    if channel is None:
        raise ValueError('Canal RSS absent')
    language = channel.findtext('language') or channel.get('{http://www.w3.org/XML/1998/namespace}lang', '')
    entries = channel.findall('item') if root.tag == 'rss' else root.findall(atom + 'entry')
    rows = []
    for item in entries[:limit]:
        def text(tag):
            el = item.find(tag)
            return ''.join(el.itertext()).strip() if el is not None else ''
        link = text('link')
        if root.tag != 'rss':
            link = next((el.get('href', '') for el in item.findall(atom+'link') if el.get('rel', 'alternate') == 'alternate'), '')
        isbn = [el.text or '' for el in item.findall(dc+'identifier') if (el.text or '').lower().startswith('urn:isbn:')]
        rows.append({'title': text('title') or text(atom+'title'),
            'url': public_join(url, link),
            'critic_author': text('author') or text(dc+'creator') or text(atom+'author/'+atom+'name'),
            'published_at': date_text(text('pubDate') or text(atom+'published') or text(atom+'updated') or text(dc+'date')),
            'excerpt': text('description') or text(atom+'summary'),
            'language': text(dc+'language') or language,
            # Never treat the critic as the author of the reviewed book.
            'book': {'title': text(bookns+'title'), 'authors': [el.text or '' for el in item.findall(bookns+'author')],
                     'isbns': [s[9:] for s in isbn], 'notice_url': public_join(url, text(bookns+'notice'))}})
    return rows


def discover_url(url, *, fetch=fetch_public):
    url = public_url(url)
    if not url:
        raise ValueError('URL HTTP(S) publique requise')
    final, body = fetch(url)
    final = public_url(final)
    if not final:
        raise ValueError('Destination finale non publique refusée')
    try:
        parse_feed(body, final, limit=1)
    except (ValueError, ET.ParseError, TypeError):
        return tuple(
            candidate for candidate in discover_feed_links(final, body.decode('utf-8', errors='replace'))
            if public_url(candidate.url)
        )
    from literary_criticism import FeedCandidate
    return (FeedCandidate(final, final, final, evidence='explicit-feed'),)


class FeedSource:
    def __init__(self, url='', *, fetch=fetch_public):
        self.url, self.fetch = public_url(url), fetch

    def activate(self, context):
        self.url = public_url(context.config.get('feed_url'))
        return self

    def source_info(self):
        if not self.url:
            raise ValueError('Configurez une URL de flux RSS/Atom')
        return {'source_id': 'feed.' + sha256(self.url.encode()).hexdigest()[:24],
                'display_name': self.url, 'homepage_url': self.url}

    def discover(self, query, limit=20):
        return discover_url(query, fetch=self.fetch)[:limit]

    def collect(self, cursor=None, limit=50):
        if cursor:
            return {'items': []}
        final, body = self.fetch(self.url)
        final = public_url(final)
        if not final:
            raise ValueError('Destination finale non publique refusée')
        return {'items': parse_feed(body, final, limit=min(500, max(1, limit))), 'fresh_for_seconds': 900}


PLUGIN = FeedSource()
