"""Public catalogue URLs only; delivery and signed links are not fallbacks."""
from urllib.parse import urlsplit, parse_qsl, unquote


def catalogue_url(row):
    metadata = row.get('metadata') or {}
    search = metadata.get('search_result') or {}
    candidates = [row.get('friendly_url'), row.get('product_url'), metadata.get('friendly_url'),
                  metadata.get('product_url'), search.get('friendly_url') if isinstance(search, dict) else None]
    for value in candidates:
        if not isinstance(value, str) or any(ord(c) < 32 for c in value): continue
        try:
            parts = urlsplit(value); port = parts.port
        except ValueError: continue
        if parts.scheme not in {'https', 'http'} or not parts.hostname or parts.username or parts.password: continue
        if port not in {None, 80, 443}: continue
        path = unquote(unquote(parts.path)).casefold()
        keys = {key.casefold() for key, _ in parse_qsl(parts.query)}
        if path.endswith(('.acsm', '.epub', '.pdf')) or any(s in path for s in ('/fulfillment/', '/assaredirect/', '/loan/')): continue
        if keys & {'token', 'signature', 'sig', 'access_token', 'auth', 'key', 'download'}: continue
        if any(key.startswith(('x-amz-', 'x-goog-')) or 'token' in key or 'signature' in key for key in keys): continue
        return value
    return None


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
        # Numilog currently exposes the authenticated personal-library index
        # as friendly_url for every holding, without a per-book public notice.
        if provider == 'numilog' and parts.path.rstrip('/').casefold() == '/pages/compte/meslivres.aspx':
            continue
        return safe
    return None
