"""Explicit HTTPX SSO POST qualification, no browser and no automatic login.

Uses the public transport facade. A caller must affirmatively provide a
candidate field mapping built from a fresh form and user-supplied credentials.
No provider session is marked connected; a POST 200 is unverified evidence.
"""
from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit
import httpx
from cifereca.plugin_api import HTTPX_TRANSPORT

from biblioaccess_http_sso import (
    ORIGIN, LOGON_PATH, FormShape, SsoProtocolError,
    BIBLIO_ORIGIN, ACCOUNT_PATH, LOANS_PATH, _url,
    inspect_login_form_actions, logon_response_shape, _logon_target,
)

_MAX_RESPONSE_BYTES = 65536


def _bounded_body(response, limit):
    result = bytearray()
    for chunk in response.iter_bytes():
        if len(result) + len(chunk) > limit:
            raise SsoProtocolError("sso_response_limit")
        result.extend(chunk)
    return bytes(result)


@dataclass(frozen=True)
class LogonObservation:
    status: int
    media_type: str
    body_bytes: int
    authentication_verified: bool = False
    cas_completed: bool = False
    post_sent: bool = True


@dataclass(frozen=True)
class CasEntryObservation:
    status: int
    route: str
    authentication_verified: bool = False


class BiblioAccessHTTPQualification:
    """Ephemeral HTTP-only client, caller owns shutdown and cancellation."""

    def __init__(self, *, transport=None):
        self.http = HTTPX_TRANSPORT.client(
            producer="provider.biblioaccess",
            timeout=httpx.Timeout(12, connect=8),
            follow_redirects=False, trust_env=False, transport=transport,
        )
        self._closed = False
        self._post_attempted = False
        self._post_lock = Lock()
        self._inspected_form_shapes = ()
        self._form_actions = {}
        self._login_inspected = False
        self._idp_login_url = None
        self._cas_service = None
        self._cas_attempted = False
        self._logon_status = None
        self._cas_landing_seen = False
        self._loans_attempted = False

    def close(self):
        if not self._closed:
            self._closed = True
            self.http.cookies.clear()
            self._inspected_form_shapes = ()
            self._form_actions = {}
            self._idp_login_url = None
            self._cas_service = None
            self.http.close()

    def inspect_cas_entry(self) -> CasEntryObservation:
        """Observe the first BiblioAccess redirect without following an IdP."""
        if self._closed:
            raise SsoProtocolError("sso_transport_closed")
        try:
            with self.http.stream(
                "GET", BIBLIO_ORIGIN + ACCOUNT_PATH,
                headers={"Accept": "text/html"},
                follow_redirects=False,
            ) as response:
                if response.status_code not in (200, 302):
                    raise SsoProtocolError("sso_entry_unavailable")
                if response.status_code == 302:
                    target = urljoin(BIBLIO_ORIGIN + ACCOUNT_PATH,
                                     response.headers.get("location", ""))
                    try:
                        p = _url(target)
                    except SsoProtocolError:
                        raise SsoProtocolError("sso_entry_redirect_refused") from None
                    if (p.hostname != "mediatheques.agglo-larochelle.fr"
                            or p.path not in ("/login", "/cas/login")):
                        raise SsoProtocolError("sso_entry_redirect_refused")
                    if p.path == "/login":
                        query = parse_qs(p.query, keep_blank_values=True)
                        if set(query) != {"service"} or len(query["service"]) != 1:
                            raise SsoProtocolError("sso_entry_redirect_refused")
                        try:
                            service = _url(query["service"][0])
                        except SsoProtocolError:
                            raise SsoProtocolError("sso_entry_redirect_refused") from None
                        if (service.hostname != "www.biblioaccess.com"
                                or not (service.path == "/1409"
                                        or service.path.startswith("/1409/"))
                                or service.query):
                            raise SsoProtocolError("sso_entry_redirect_refused")
                        self._idp_login_url = target
                        self._cas_service = query["service"][0]
                    return CasEntryObservation(
                        status=302, route="institutional_redirect_unverified")
                # A landing 200 may represent an already connected user, or
                # a login shell. Never infer identity from this.
                return CasEntryObservation(status=200, route="entry_unverified")
        except (httpx.HTTPError, UnicodeError):
            raise SsoProtocolError("sso_transport_unavailable") from None

    def inspect_login(self) -> tuple[FormShape, ...]:
        if self._closed:
            raise SsoProtocolError("sso_transport_closed")
        try:
            request_url = self._idp_login_url or ORIGIN + "/login"
            with self.http.stream("GET", request_url,
                                  headers={"Accept": "text/html"},
                                  follow_redirects=False) as response:
                if response.status_code != 200:
                    raise SsoProtocolError("sso_login_unavailable")
                media = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                if media not in ("text/html", "application/xhtml+xml"):
                    raise SsoProtocolError("sso_unexpected_content")
                body = _bounded_body(response, 256000)
                pairs = inspect_login_form_actions(
                    request_url, body.decode("utf-8", errors="strict"))
                shapes = tuple(shape for shape, _ in pairs)
                self._form_actions = {id(shape): target for shape, target in pairs}
                self._inspected_form_shapes = shapes
                self._login_inspected = True
                return shapes
        except (httpx.HTTPError, UnicodeError):
            raise SsoProtocolError("sso_transport_unavailable") from None

    def submit_once(
        self, *, shape: FormShape, fields: dict[str, str],
        explicit_consent: bool,
    ) -> LogonObservation:
        """One unverified POST, never a retry or a Core authentication."""
        if self._closed:
            raise SsoProtocolError("sso_transport_closed")
        if explicit_consent is not True:
            raise SsoProtocolError("sso_consent_required")
        if (type(shape) is not FormShape or shape.method != "POST"
                or shape.action_origin != ORIGIN
                or not shape.action_path.endswith("/logon")
                or type(fields) is not dict
                or set(fields) != set(shape.field_names)
                or any(type(k) is not str or type(v) is not str or len(v) > 8192
                       for k, v in fields.items())
                or not shape.password_field_present
                or not self._login_inspected
                or not any(shape is inspected for inspected in self._inspected_form_shapes)
                or id(shape) not in self._form_actions):
            raise SsoProtocolError("sso_post_schema_unqualified")
        # Mark before any I/O: an uncertain POST must never retry on this client.
        with self._post_lock:
            if self._post_attempted:
                raise SsoProtocolError("sso_post_already_attempted")
            self._post_attempted = True
        target = self._form_actions[id(shape)]
        _logon_target(target)
        try:
            with self.http.stream(
                "POST", target, data=fields,
                headers={"Accept": "application/json, text/html"},
                follow_redirects=False,
            ) as response:
                body = _bounded_body(response, _MAX_RESPONSE_BYTES)
                observed = logon_response_shape(
                    status=response.status_code,
                    content_type=response.headers.get("content-type", ""),
                    body=body,
                )
                self._logon_status = observed["status"]
                return LogonObservation(
                    status=observed["status"],
                    media_type=observed["media_type"],
                    body_bytes=observed["body_bytes"],
                )
        except (httpx.HTTPError, UnicodeError):
            # A POST could already have reached the server. No implicit retry.
            raise SsoProtocolError("sso_post_outcome_unknown") from None

    def inspect_cas_return_once(self) -> CasEntryObservation:
        """One bounded CAS GET and one same-session ticket landing GET.

        Never returns or caches the service ticket, and never marks the
        provider connected. A POST 200 is not an account proof. No browser.
        """
        if self._closed:
            raise SsoProtocolError("sso_transport_closed")
        with self._post_lock:
            if self._cas_attempted:
                raise SsoProtocolError("sso_cas_already_attempted")
            if (not self._cas_service or not self._login_inspected
                    or self._logon_status != 200):
                raise SsoProtocolError("sso_cas_unqualified")
            self._cas_attempted = True
        request_url = ORIGIN + "/cas/login?" + urlencode({"service": self._cas_service})
        try:
            with self.http.stream("GET", request_url, follow_redirects=False,
                                  headers={"Accept": "text/html"}) as response:
                if response.status_code != 302:
                    raise SsoProtocolError("sso_cas_return_unverified")
                location = response.headers.get("location", "")
                if not location:
                    raise SsoProtocolError("sso_cas_return_refused")
                target = urljoin(request_url, location)
                try:
                    p = _url(target)
                except SsoProtocolError:
                    raise SsoProtocolError("sso_cas_return_refused") from None
                query = parse_qs(p.query, keep_blank_values=True)
                if (p.hostname != "www.biblioaccess.com"
                        or p.path != urlsplit(self._cas_service).path
                        or set(query) != {"ticket"} or len(query["ticket"]) != 1
                        or not 1 <= len(query["ticket"][0]) <= 2048):
                    raise SsoProtocolError("sso_cas_return_refused")
            # Ticket goes only to its exact validated BiblioAccess tenant,
            # once, in the live HTTPX session. The receipt omits its URL.
            with self.http.stream("GET", target, follow_redirects=False,
                                  headers={"Accept": "text/html"}) as landing:
                if landing.status_code != 200:
                    raise SsoProtocolError("sso_cas_landing_unverified")
                self._cas_landing_seen = True
                return CasEntryObservation(
                    status=200, route="cas_landing_unverified")
        except (httpx.HTTPError, UnicodeError):
            raise SsoProtocolError("sso_cas_transport_unavailable") from None

    def inspect_loans_once(self) -> CasEntryObservation:
        """Read one bounded tenant-1409 loans page after the same CAS chain.

        No data is returned or persisted; status 200 is not a claim that the
        page belongs to a verified account. No action other than HTTPS GET.
        """
        if self._closed:
            raise SsoProtocolError("sso_transport_closed")
        with self._post_lock:
            if self._loans_attempted:
                raise SsoProtocolError("sso_loans_already_attempted")
            if not self._cas_landing_seen:
                raise SsoProtocolError("sso_loans_session_unverified")
            self._loans_attempted = True
        try:
            with self.http.stream(
                "GET", BIBLIO_ORIGIN + LOANS_PATH,
                headers={"Accept": "text/html"},
                follow_redirects=False,
            ) as response:
                if response.status_code != 200:
                    raise SsoProtocolError("sso_loans_session_unverified")
                media = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                if media not in ("text/html", "application/xhtml+xml"):
                    raise SsoProtocolError("sso_loans_unexpected_content")
                _bounded_body(response, 256000)
                return CasEntryObservation(status=200, route="loans_html_unverified")
        except (httpx.HTTPError, UnicodeError):
            raise SsoProtocolError("sso_loans_transport_unavailable") from None
