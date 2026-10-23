"""HTTP reader session. No browser, private API or exported SSO state."""
from __future__ import annotations

import re
import time
from urllib.parse import urljoin, urlsplit
import httpx
from cifereca_bnf_pret_web import _DOM, MAX_HTML_BYTES, WebParseError

ORIGIN = 'https://pret.bnf.fr'
ACTIVITY = ORIGIN + '/my_profile/activity'


class ReaderHTTPError(RuntimeError):
    """Only closed codes may leave this transport boundary."""


def allowed_url(url: str, *, authenticating: bool = False) -> str:
    try:
        u = urlsplit(url)
        if (len(url) > 16384 or any(ord(c) <= 32 or c == '\\' for c in url)
                or u.scheme != 'https' or u.username or u.password or u.fragment
                or u.port not in (None, 443)):
            raise ValueError
        allowed = False
        if u.hostname == 'pret.bnf.fr':
            allowed = (u.path == '/my_profile/activity'
                       or bool(re.fullmatch(r'/resources/[A-Za-z0-9_-]{1,128}', u.path)))
            if authenticating:
                allowed |= u.path.startswith('/sso/saml/')
        elif authenticating and u.hostname == 'idppub.bnf.fr':
            allowed = (u.path.startswith('/idp/profile/')
                       or bool(re.fullmatch(r'/idp/Authn/ExtCas(?:;jsessionid=[A-Za-z0-9_-]+)?', u.path)))
        elif authenticating and u.hostname == 'authentification.bnf.fr':
            allowed = u.path == '/login'
        if not allowed:
            raise ValueError
    except (ValueError, TypeError):
        raise ReaderHTTPError('url_refused') from None
    return url


def forms(html: str):
    # Same bounded DOM parser as the offline projections, without their login guard.
    if len(html) > MAX_HTML_BYTES or len(html.encode('utf-8')) > MAX_HTML_BYTES:
        raise ReaderHTTPError('html_limit')
    p = _DOM()
    try:
        p.feed(html)
        p.close()
    except (WebParseError, ValueError):
        raise ReaderHTTPError('invalid_dom') from None
    return [n for n in p.root.walk() if n.tag == 'form' and n.closed]


def form_fields(form):
    result = {}
    for n in form.walk():
        if n.tag != 'input' or 'disabled' in n.attrs:
            continue
        name = n.attrs.get('name', '')
        kind = n.attrs.get('type', 'text').lower()
        if not name or kind in {'submit', 'button', 'checkbox', 'radio', 'file'}:
            continue
        if name in result or len(result) >= 64:
            raise ReaderHTTPError('unsupported_form')
        result[name] = n.attrs.get('value', '')
    return result


class ReaderHTTPClient:
    def __init__(self, *, transport=None):
        self.http = httpx.Client(timeout=httpx.Timeout(15, connect=10),
                                 follow_redirects=False, transport=transport)

    def close(self):
        self.http.close()

    def clear(self):
        self.http.cookies.clear()

    def request(self, method, url, *, data=None, authenticating=False, deadline=None):
        deadline = deadline or time.monotonic() + 30
        for _ in range(12):
            if time.monotonic() >= deadline:
                raise ReaderHTTPError("operation_timeout")
            allowed_url(url, authenticating=authenticating)
            if method == 'POST' and not authenticating:
                raise ReaderHTTPError('mutation_refused')
            try:
                with self.http.stream(method, url, data=data,
                                      headers={'Accept': 'text/html'},
                                      timeout=min(15, max(0.1, deadline-time.monotonic()))) as r:
                    if r.status_code in {301, 302, 303, 307, 308}:
                        if method == 'POST' and r.status_code in {307, 308}:
                            raise ReaderHTTPError('post_redirect_refused')
                        location = r.headers.get('location')
                        if not location:
                            raise ReaderHTTPError('redirect_invalid')
                        try:
                            url = urljoin(url, location)
                        except ValueError:
                            raise ReaderHTTPError('redirect_invalid') from None
                        method, data = 'GET', None
                        continue
                    if r.status_code != 200:
                        code = {401: 'auth_required', 403: 'access_denied', 429: 'rate_limited'}.get(r.status_code, 'http_unavailable')
                        raise ReaderHTTPError(code)
                    if r.headers.get('content-type', '').split(';')[0].strip() not in {'text/html', 'application/xhtml+xml'}:
                        raise ReaderHTTPError('unexpected_content')
                    body = bytearray()
                    for chunk in r.iter_bytes():
                        if time.monotonic() >= deadline:
                            raise ReaderHTTPError("operation_timeout")
                        if len(body) + len(chunk) > MAX_HTML_BYTES:
                            raise ReaderHTTPError('html_limit')
                        body.extend(chunk)
                    return str(r.url), body.decode(r.encoding or 'utf-8', errors='strict')
            except (httpx.HTTPError, UnicodeError):
                raise ReaderHTTPError('transport_unavailable') from None
        raise ReaderHTTPError('redirect_limit')

    def login(self, username, password):
        self.clear()
        deadline = time.monotonic() + 60
        url, html = self.request('GET', ACTIVITY, authenticating=True, deadline=deadline)
        submitted = False
        for _ in range(8):
            if url == ACTIVITY:
                return html
            candidates = forms(html)
            if len(candidates) != 1:
                raise ReaderHTTPError('auth_interactive_required')
            form = candidates[0]
            fields = form_fields(form)
            target = allowed_url(urljoin(url, form.attrs.get('action', '')), authenticating=True)
            if form.attrs.get('method', 'get').lower() != 'post':
                raise ReaderHTTPError('unsupported_form')
            if 'password' in fields:
                if submitted:
                    raise ReaderHTTPError('authentication_failed')
                if urlsplit(target).hostname != 'authentification.bnf.fr' or 'username' not in fields:
                    raise ReaderHTTPError('unsupported_form')
                fields.update(username=username, password=password)
                submitted = True
            elif 'SAMLResponse' in fields:
                # A freshly issued response in THIS session, never imported or retained.
                if (not submitted or urlsplit(target).hostname != 'pret.bnf.fr'
                        or not urlsplit(target).path.startswith('/sso/saml/')):
                    raise ReaderHTTPError('unsupported_form')
            else:
                raise ReaderHTTPError('auth_interactive_required')
            url, html = self.request('POST', target, data=fields, authenticating=True, deadline=deadline)
        raise ReaderHTTPError('auth_step_limit')

    def activity(self):
        return self.request('GET', ACTIVITY)[1]
