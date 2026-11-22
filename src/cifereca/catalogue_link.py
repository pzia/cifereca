"""Public catalogue URLs only; delivery and signed links are not fallbacks."""
from urllib.parse import urlsplit, parse_qsl, unquote


def _sensitive_parameters(text):
    keys = set()
    for key, _ in parse_qsl(text.replace(';', '&'), keep_blank_values=True):
        # Do not let nested escapes hide a sensitive parameter name from
        # this boundary. A bounded decode suffices for ordinary names;
        # unresolved escapes or encoded parameter delimiters are ambiguous.
        for _ in range(4):
            decoded = unquote(key)
            if decoded == key:
                break
            key = decoded
        if (any(marker in key for marker in ('%', '&', ';', '=', '\\x00'))
                or any(ord(char) < 32 or ord(char) == 127 for char in key)):
            return True
        keys.add(key.casefold())
    normalized = {''.join(char for char in key if char.isalnum()) for key in keys}
    if normalized & {
        'token', 'signature', 'sig', 'accesstoken', 'auth', 'key',
        'download', 'authorization', 'session', 'sessionid', 'sessionkey',
        'ticket', 'credential', 'credentials', 'secret', 'password',
        'apikey', 'expires', 'policy', 'keypairid', 'awsaccesskeyid',
        'googleaccessid', 'jwt',
    }:
        return True
    return any(
        key.startswith(('xamz', 'xgoog'))
        or 'token' in key or 'signature' in key
        for key in normalized
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
        # Validate the decoded path rather than treating a trailing slash,
        # matrix parameter or encoded separator as a different document.
        # A residual escape or control is ambiguous at the browser boundary.
        if ('%' in path or '\\' in path or ';' in path
                or any(ord(char) < 32 or ord(char) == 127 for char in path)):
            continue
        segments = set(path.split('/'))
        if (segments & {'.', '..', 'fulfillment', 'assaredirect', 'loan',
                         'loans', 'borrow', 'borrows', 'download', 'downloads',
                         'acquisition', 'acquire', 'readbook.ashx'}
                or '/pages/compte/' in path
                or path.rstrip('/').endswith(('/pages/compte', '.acsm',
                                               '.lcpl', '.epub', '.pdf', '.zip'))):
            continue
        if _sensitive_parameters(parts.query) or _sensitive_parameters(parts.fragment):
            continue
        return value
    return None


def safe_document_link_url(value, *, relation='related'):
    """Validate an externally supplied detail-panel link at the render boundary."""
    # Explicit DocumentLink DTOs (including retained plugin detail) can
    # bypass the row projector. Apply the same notice-shape refusals at
    # the last render boundary; generic navigation remains distinct.
    if str(relation or '').casefold() == 'notice':
        return catalogue_notice_url({'friendly_url': value})
    return catalogue_url({'friendly_url': value})


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
        if not isinstance(value, str) or not value or value != value.strip():
            continue
        # urljoin/urlsplit may silently remove whitespace or control
        # characters: reject the original value BEFORE relative resolution.
        if any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value):
            continue
        candidate = urljoin(base, value) if reference else value
        safe = catalogue_url({'friendly_url': candidate})
        if not safe:
            continue
        parts = urlsplit(safe)
        # A bare domain or a catalogue root is never a notice.
        # A bare origin remains a catalogue entry point even when it
        # carries a harmless search, tracking or routing query. Query-only
        # coordinates do not attest a book-specific public HTML sheet.
        if not parts.path.strip('/'):
            continue
        if reference and page_key(safe) == page_key(reference):
            continue
        # Feed, fetch and Atom/OPDS endpoints are not book-specific public
        # notices, irrespective of who supplied the cached URL. The generic
        # catalogue_url guard already excludes private /pages/compte/ paths.
        path = unquote(unquote(parts.path)).casefold().rstrip('/')
        if (path.endswith(('.xml', '.atom', '.opds'))
                or path in {'/feed', '/feed.php', '/fetch.php',
                            '/opds', '/opds/search', '/services/engine/search/opds',
                            # These are search/result endpoints, not a
                            # per-book public HTML sheet, even when a
                            # producer has a catalog URL and a series query.
                            '/search', '/search.aspx', '/search.svc/search',
                            '/index.php/search', '/index.php/search.aspx',
                            '/ebooks/search',
                            '/index.php/feed', '/index.php/feed.php',
                            '/index.php/fetch', '/index.php/fetch.php',
                            '/index.php/opensearch.xml'}
                or path.startswith(('/feed/', '/fetch/',
                                    '/index.php/feed/',
                                    '/index.php/fetch/'))
                or '%' in path):
            continue
        return safe
    return None
