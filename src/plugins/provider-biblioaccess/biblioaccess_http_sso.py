"""La Rochelle CAS-like HTTP qualification without browser or guessed login.

No network is initiated at import, plugin discovery, or session creation.
This module exposes strictly bounded, opt-in structure observations to qualify
the institutional /logon exchange before a live authentication implementation.

Diagnostics describe *only the wire shape*, never form values, headers or
response bodies. Provider sessions remain disabled until a supported login
protocol has been evidenced.
"""
from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
import re
from urllib.parse import urljoin, urlsplit

ORIGIN = "https://mediatheques.agglo-larochelle.fr"
BIBLIO_ORIGIN = "https://www.biblioaccess.com"
LOGIN_PATH = "/login"
LOGON_PATH = "/logon.svc/logon"
CAS_PATH = "/cas/login"
ACCOUNT_PATH = "/1409/Account/Login"
LOANS_PATH = "/1409/Catalog/MyLoans"
MAX_HTML = 256_000
MAX_FIELDS = 32
MAX_NAME = 64
MAX_FORMS = 8
_ALLOWED_METHODS = frozenset(("GET", "POST"))


class SsoProtocolError(ValueError):
    """Closed error codes, never interpolate untrusted requests/responses."""


def _url(url: str):
    if (type(url) is not str or len(url) > 4096 or not url
            or any(ord(c) <= 32 or ord(c) == 127 or c == "\\" for c in url)):
        raise SsoProtocolError("sso_url_refused")
    try:
        p = urlsplit(url)
        if (p.scheme != "https" or p.username is not None
                or p.password is not None or p.port not in (None, 443)
                or p.fragment or p.hostname not in (
                    "mediatheques.agglo-larochelle.fr",
                    "www.biblioaccess.com",
                )):
            raise SsoProtocolError("sso_url_refused")
    except ValueError:
        raise SsoProtocolError("sso_url_refused") from None
    return p


def classify_http_step(method: str, url: str, status: int, *, location: str = "") -> str:
    """Observe only known host/path/status combinations, no ticket retention."""
    if type(status) is not int or type(method) is not str:
        return "unsupported"
    try:
        p = _url(url)
    except SsoProtocolError:
        return "unsupported"
    if status in (401, 403):
        return "authentication_refused"
    if status == 429:
        return "rate_limited"
    if status >= 500:
        return "server_unavailable"
    if p.hostname == "mediatheques.agglo-larochelle.fr":
        if (method, p.path, status) == ("GET", LOGIN_PATH, 200):
            return "login_form_unverified"
        if (method, p.path, status) == ("POST", LOGON_PATH, 200):
            # A 200 with JSON is NOT verified credentials or CAS completion.
            return "logon_response_unverified"
        if (method, p.path, status) == ("GET", CAS_PATH, 302):
            try:
                target = _url(urljoin(url, location))
            except SsoProtocolError:
                return "cas_redirect_refused"
            return ("cas_redirect_unverified" if target.hostname ==
                    "www.biblioaccess.com" and target.path.startswith("/1409/")
                    else "cas_redirect_refused")
    if p.hostname == "www.biblioaccess.com":
        if (method, p.path, status) == ("GET", ACCOUNT_PATH, 302):
            try:
                target = _url(urljoin(url, location))
            except SsoProtocolError:
                return "sso_redirect_refused"
            return ("sso_redirect_unverified" if target.hostname ==
                    "mediatheques.agglo-larochelle.fr"
                    and target.path in (LOGIN_PATH, CAS_PATH)
                    else "sso_redirect_refused")
        if (method, p.path, status) == ("GET", LOANS_PATH, 200):
            return "loans_html_unverified"
    return "unsupported"


@dataclass(frozen=True)
class FormShape:
    method: str
    action_origin: str
    action_path: str
    field_names: tuple[str, ...]
    password_field_present: bool
    submit_ready: bool = False  # Not evidence of a complete login contract.


class _LoginFormParser(HTMLParser):
    def __init__(self, page_url):
        super().__init__(convert_charrefs=True)
        self.page_url = page_url
        self.forms = []
        self.current = None
        self.invalid = False

    def handle_starttag(self, tag, attrs):
        if len({k for k, _ in attrs}) != len(attrs):
            self.invalid = True
            return
        a = dict(attrs)
        if tag == "form":
            if self.current is not None or len(self.forms) >= MAX_FORMS:
                self.invalid = True
                return
            self.current = dict(method=(a.get("method") or "GET").upper(),
                                action=a.get("action") or self.page_url,
                                names=[], password=False)
        elif tag == "input" and self.current is not None:
            name = a.get("name") or ""
            if not name:
                return
            if (len(name) > MAX_NAME
                    or not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9.:-]*", name)
                    or len(self.current["names"]) >= MAX_FIELDS
                    or name in self.current["names"]):
                self.invalid = True
                return
            self.current["names"].append(name)
            self.current["password"] |= a.get("type", "").lower() == "password"

    def handle_endtag(self, tag):
        if tag == "form" and self.current is not None:
            self.forms.append(self.current)
            self.current = None


def inspect_login_forms(page_url: str, html: str) -> tuple[FormShape, ...]:
    """Document only form structure; never preserve input values or HTML."""
    p = _url(page_url)
    if p.hostname != "mediatheques.agglo-larochelle.fr" or p.path != LOGIN_PATH:
        raise SsoProtocolError("sso_login_page_refused")
    if type(html) is not str or len(html) > MAX_HTML:
        raise SsoProtocolError("sso_html_limit")
    parser = _LoginFormParser(page_url)
    try:
        parser.feed(html)
        parser.close()
    except ValueError:
        raise SsoProtocolError("sso_invalid_form") from None
    if parser.invalid or parser.current is not None:
        raise SsoProtocolError("sso_invalid_form")
    shapes = []
    for form in parser.forms:
        target = urljoin(page_url, form["action"])
        try:
            u = _url(target)
        except SsoProtocolError:
            raise SsoProtocolError("sso_form_target_refused") from None
        if (u.hostname != "mediatheques.agglo-larochelle.fr"
                or u.path != LOGON_PATH or u.query
                or form["method"] not in _ALLOWED_METHODS):
            raise SsoProtocolError("sso_form_target_refused")
        shapes.append(FormShape(
            method=form["method"], action_origin=ORIGIN,
            action_path=LOGON_PATH, field_names=tuple(form["names"]),
            password_field_present=form["password"]))
    return tuple(shapes)


def logon_response_shape(*, status: int, content_type: str,
                         body: bytes) -> dict[str, object]:
    """Minimal response shape; do not return HTTP body or JSON values."""
    if type(body) is not bytes or len(body) > 65536:
        raise SsoProtocolError("sso_response_limit")
    if type(content_type) is not str or len(content_type) > 256:
        raise SsoProtocolError("sso_response_type_invalid")
    media = content_type.split(";", 1)[0].strip().lower()
    if media not in ("application/json", "text/html", "application/xhtml+xml"):
        raise SsoProtocolError("sso_response_type_invalid")
    if type(status) is not int or not 100 <= status <= 599:
        raise SsoProtocolError("sso_invalid_status")
    # Do not emit arbitrary JSON keys: a supplier can return auth tokens or
    # confidential identifiers under unforeseen names.
    return {"step": "institutional_logon", "status": status,
            "media_type": media, "body_bytes": len(body),
            "authentication_verified": False,
            "cas_completed": False}
