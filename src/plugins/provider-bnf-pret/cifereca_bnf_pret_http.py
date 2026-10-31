"""HTTP reader session. No browser, private API or exported SSO state."""
from __future__ import annotations

from contextlib import contextmanager
import json
import logging
import re
from uuid import uuid4
import time
import traceback
from urllib.parse import urljoin, urlsplit
import httpx
from cifereca.plugin_api import HTTPX_TRANSPORT
from cifereca_bnf_pret_web import _DOM, MAX_HTML_BYTES, WebParseError
from cifereca_bnf_pret_paging import _params
from cifereca_bnf_pret_signed_action import signed_loan_url

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
            allowed = (not u.query and (u.path == '/my_profile/activity'
                       or bool(re.fullmatch(r'/resources/[A-Za-z0-9_-]{1,128}', u.path))))
            if u.path == '/results':
                try:
                    params = _params(url)
                    allowed = ('q' in params and 2 <= len(params['q'].strip()) <= 128)
                except WebParseError:
                    allowed = False
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
    def __init__(self, *, transport=None, diagnostics=None):
        self.diagnostics = diagnostics
        self.http = HTTPX_TRANSPORT.client(producer="provider.bnf-pret",
                                 timeout=httpx.Timeout(15, connect=10),
                                 follow_redirects=False, trust_env=False,
                                 transport=transport)

    def _observe(self, detail, status, started):
        """One canonical journal event; optional diagnostics cannot affect I/O."""
        duration_ms = max(0, int((time.monotonic() - started) * 1000))
        try:
            if self.diagnostics is not None:
                self.diagnostics.add('API', 'BnF HTTP', status,
                                     duration_ms=duration_ms, detail=detail)
            else:
                payload = dict(schema_version=1, category='API', operation='BnF HTTP',
                               message='BnF HTTP', status=status,
                               duration_ms=duration_ms, detail=detail)
                logging.getLogger('cifereca').log(
                    logging.ERROR if status == 'failed' else logging.INFO,
                    'DIAG_JSON %s', json.dumps(payload, ensure_ascii=False))
        except Exception:
            pass

    @contextmanager
    def _stream(self, method, url, *, logical_endpoint=None, **kwargs):
        # Observe only existing activity/token reads, never SSO form traffic.
        if logical_endpoint is None:
            with self.http.stream(method, url, **kwargs) as response:
                yield response
            return
        started = time.monotonic()
        detail = dict(provider_id='bnf-pret', logical_endpoint=logical_endpoint,
                      phase=({'activity': 'activity_recheck', 'search': 'catalog_search',
                              'loan_token': 'loan_token_get'}[logical_endpoint]),
                      method=method,
                      request_ref=uuid4().hex)
        self._observe(dict(detail, event='begin'), 'running', started)
        try:
            with self.http.stream(method, url, **kwargs) as response:
                detail.update(http_status=response.status_code,
                              content_type=response.headers.get('content-type', ''))
                self._observe(dict(detail, event='response'), 'running', started)
                yield response
        except Exception as exc:
            code = (str(exc) if isinstance(exc, ReaderHTTPError) else
                    'transport_unavailable' if isinstance(exc, (httpx.HTTPError, UnicodeError))
                    else 'unexpected_failure')
            # Keep the compatibility error code but never replace the raw
            # technical cause in the one canonical diagnostic event. This is
            # the exception already observed, not a new HTTP capture.
            self._observe(dict(
                detail, event='terminal', code=code,
                error_type=type(exc).__name__, error_message=str(exc),
                error_traceback=traceback.format_exc(),
            ), 'failed', started)
            raise
        else:
            # This says only that the bounded HTTP read completed, not that
            # the XML is valid, a right exists, or ADEPT succeeded.
            redirect = 300 <= detail['http_status'] < 400
            self._observe(dict(detail, event='terminal',
                               code='redirect_received' if redirect else 'http_read_completed'),
                          'partial' if redirect else 'success', started)

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
                with self._stream(method, url, data=data,
                                      logical_endpoint=(
                                          'activity' if url == ACTIVITY and not authenticating
                                          else 'search' if method == 'GET' and not authenticating
                                          and urlsplit(url).hostname == 'pret.bnf.fr'
                                          and urlsplit(url).path == '/results'
                                          else None),
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

    def loan_token(self, loan_id: str, signed_url: str) -> bytes:
        """One non-retried GET for a loan previously checked by its session.

        Unlike HTML requests, this never follows a redirect (even to BnF).
        The response is bounded before buffering and independently inspected
        as an ADEPT loan token by the caller; no private URL is returned.
        """
        from cifereca_bnf_pret_download import MAX_TOKEN_BYTES, exact_loan_id
        loan_id = exact_loan_id(loan_id)
        # Use the signed action observed in the fresh authenticated page.
        # Never construct nonce/timestamp/token or reuse a signed URL from
        # another loan: take the link directly from fresh activity HTML.
        url = signed_loan_url(signed_url, loan_id)
        if url is None or url != signed_url:
            raise ReaderHTTPError('url_refused')
        deadline = time.monotonic() + 30
        try:
            with self._stream(
                'GET', url, logical_endpoint='loan_token', headers={'Accept': 'application/vnd.adobe.adept+xml, application/octet-stream'},
                timeout=min(15, max(0.1, deadline - time.monotonic())),
            ) as response:
                if 300 <= response.status_code < 400:
                    raise ReaderHTTPError('download_redirect_refused')
                if response.status_code != 200:
                    code = {401: 'auth_required', 403: 'access_denied',
                            429: 'rate_limited'}.get(response.status_code, 'http_unavailable')
                    raise ReaderHTTPError(code)
                length = response.headers.get('content-length', '')
                if length:
                    try:
                        declared = int(length)
                    except (ValueError, TypeError):
                        raise ReaderHTTPError('invalid_loan_token') from None
                    if declared <= 0:
                        raise ReaderHTTPError('invalid_loan_token')
                    if declared > MAX_TOKEN_BYTES:
                        raise ReaderHTTPError('token_limit')
                mime = response.headers.get('content-type', '').partition(';')[0].strip().lower()
                if mime in {'text/html', 'application/xhtml+xml'}:
                    raise ReaderHTTPError('invalid_loan_token')
                data = bytearray()
                for chunk in response.iter_bytes():
                    if time.monotonic() >= deadline:
                        raise ReaderHTTPError('operation_timeout')
                    if len(data) + len(chunk) > MAX_TOKEN_BYTES:
                        raise ReaderHTTPError('token_limit')
                    data.extend(chunk)
                if not data:
                    raise ReaderHTTPError('invalid_loan_token')
                return bytes(data)
        except httpx.HTTPError:
            # Errors may carry a private loan URL or response metadata.
            raise ReaderHTTPError('transport_unavailable') from None

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
