"""Public catalogue URLs only; delivery and signed links are not fallbacks."""
from urllib.parse import urlsplit, parse_qsl, unquote


def _sensitive_parameters(text):
    keys = {unquote(key).casefold() for key, _ in parse_qsl(
        text.replace(';', '&'), keep_blank_values=True,
    )}
    if keys & {'token', 'signature', 'sig', 'access_token', 'auth', 'key', 'download', 'authorization'}:
        return True
    return any(
        key.startswith(('x-amz-', 'x-goog-'))
        or 'token' in key or 'signature' in key
        for key in keys
    )


def catalogue_url(row):
    metadata = row.get('metadata')
    metadata = metadata if isinstance(metadata, dict) else {}
    search = metadata.get('search_result')
    search = search if isinstance(search, dict) else {}
    candidates = (
        row.get('friendly_url'), row.get('product_url'),
        metadata.get('friendly_url'), metadata.get('product_url'),
        search.get('friendly_url'),
    )
    for value in candidates:
        if not isinstance(value, str) or not value or value != value.strip():
            continue
        # urlsplit can normalize away leading C0 and embedded tabs/newlines.
        # The exact URL passed to the browser must be the one validated here.
        if any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value) or '\\' in value:
            continue
        try:
            parts = urlsplit(value)
            port = parts.port
            userinfo = parts.username is not None or parts.password is not None
        except ValueError:
            continue
        if parts.scheme not in {'https', 'http'} or not parts.hostname or userinfo:
            continue
        if port not in {None, 80, 443}:
            continue
        path = unquote(unquote(parts.path)).casefold()
        if path.endswith(('.acsm', '.epub', '.pdf')):
            continue
        if any(marker in path for marker in (
            '/fulfillment/', '/assaredirect/', '/loan/',
            '/download/', '/downloads/', '/pages/compte/',
        )) or path.endswith(('/download', '/downloads', '/readbook.ashx')):
            continue
        if _sensitive_parameters(parts.query) or _sensitive_parameters(parts.fragment):
            continue
        return value
    return None


def safe_document_link_url(value, *, relation='related'):
    """Validate an externally supplied detail-panel link at the render boundary."""
    url = catalogue_url({'friendly_url': value})
    if not url:
        return None
    if str(relation or '').casefold() == 'notice':
        parts = urlsplit(url)
        # A bare domain/catalogue homepage cannot be presented as a notice.
        if not parts.path.strip('/') and not parts.query:
            return None
    return url


def catalogue_notice_url(row, *, base=None, provider=None):
    """Return an attested book-specific public URL, never a catalogue entry point.

    A provider may expose its homepage or a private 'my books' index as a
    friendly_url. Those are useful navigation targets, but not notices of the
    book represented by this row.
    """
    from urllib.parse import urljoin

    metadata = row.get('metadata')
    metadata = metadata if isinstance(metadata, dict) else {}
    search = metadata.get('search_result')
    search = search if isinstance(search, dict) else {}
    candidates = (
        row.get('friendly_url'), row.get('product_url'),
        metadata.get('friendly_url'), metadata.get('product_url'),
        search.get('friendly_url'), search.get('product_url'),
    )
    reference = catalogue_url({'friendly_url': base}) if base else None

    def page_key(value):
        parts = urlsplit(value)
        return (parts.scheme.lower(), (parts.hostname or '').lower(),
                parts.port, parts.path.rstrip('/').casefold(), parts.query)

    for value in candidates:
        if not isinstance(value, str) or not value.strip():
            continue
        # urljoin/urlsplit may silently remove tabs or line breaks: reject the
        # original provider value *before* resolving any relative URL.
        if any(ord(char) < 32 or ord(char) == 127 for char in value):
            continue
        candidate = urljoin(base, value) if reference else value
        safe = catalogue_url({'friendly_url': candidate})
        if not safe:
            continue
        parts = urlsplit(safe)
        # A bare domain or a catalogue root is never a notice.
        if not parts.path.strip('/') and not parts.query:
            continue
        if reference and page_key(safe) == page_key(reference):
            continue
        # Historical EbooksGratuits OPDS rows used the search/feed URL as
        # friendly_url when no HTML alternate existed. Those rows can still
        # reside in Core's catalogue cache after the provider fix (#2460).
        # At the presentation boundary a COPS Atom/feed/fetch endpoint can
        # NEVER become a readable book notice, even when stored as a
        # friendly_url or nested product_url. No cache migration or HTTP GET.
        if provider == 'ebooksgratuits':
            path = unquote(unquote(parts.path)).casefold().rstrip('/')
            if (path.endswith(('.xml', '.atom', '.opds'))
                    or path in {'/feed', '/feed.php', '/fetch.php',
                                '/index.php/feed', '/index.php/feed.php',
                                '/index.php/fetch', '/index.php/fetch.php',
                                '/index.php/opensearch.xml'}
                    or path.startswith(('/index.php/feed/',
                                        '/index.php/fetch/'))):
                continue
        # Numilog currently exposes the authenticated personal-library index
        # as friendly_url for every holding, without a per-book public notice.
        if provider == 'numilog' and parts.path.rstrip('/').casefold() == '/pages/compte/meslivres.aspx':
            continue
        return safe
    return None
