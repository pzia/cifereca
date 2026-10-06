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
