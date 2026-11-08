"""Read-only CAS continuation for the observed institutional AJAX logon.

No browser and no guessed POST fields. The AJAX POST itself is implemented in
biblioaccess_ajax_logon; this wrapper reuses the SAME HTTPX cookie jar, pins
the CAS service from the provider's fresh login redirect, and never reports
Core authentication or a library right. No networking at import time.
"""
from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit

import httpx

from biblioaccess_ajax_logon import ObservedAjaxLogonClient, AjaxLogonError
from biblioaccess_ajax_preflight import inspect_login_preflight, LoginPreflight

BIBLIO = "https://www.biblioaccess.com"
IDP = "https://mediatheques.agglo-larochelle.fr"
ENTRY = BIBLIO + "/1409/Account/Login"
CAS = IDP + "/cas/login"
LOANS = BIBLIO + "/1409/Catalog/MyLoans"
_LIMIT = 256 * 1024


class AjaxCasError(ValueError):
    """Stable codes only, never show a CAS ticket or a request URL."""


@dataclass(frozen=True)
class CasHttpObservation:
    phase: str
    status: int
    connected: bool = False
    identity_verified: bool = False
    loans_verified: bool = False


def _url(url: str):
    if (type(url) is not str or len(url) > 4096
            or any(ord(ch) <= 32 or ord(ch) == 127 or ch == "\\" for ch in url)):
        raise AjaxCasError("cas_url_refused")
    try:
        u = urlsplit(url)
        if (u.scheme != "https" or u.username is not None
                or u.password is not None or u.port not in (None, 443)
                or u.fragment or u.hostname not in (
                    "www.biblioaccess.com", "mediatheques.agglo-larochelle.fr")):
            raise AjaxCasError("cas_url_refused")
        return u
    except ValueError:
        raise AjaxCasError("cas_url_refused") from None


def _bounded(response, limit=_LIMIT):
    content = bytearray()
    for block in response.iter_bytes():
        if len(content) + len(block) > limit:
            raise AjaxCasError("cas_body_limit")
        content.extend(block)
    return bytes(content)


class ObservedAjaxCasClient(ObservedAjaxLogonClient):
    """One transaction per instance; CAS service and tickets stay in RAM."""

    def __init__(self, *, transport=None):
        super().__init__(transport=transport)
        self._cas_lock = Lock()
        self._service = None
        self._login_url = None
        self._login_seen = False
        self._preflight = None
        self._entry_attempted = False
        self._cas_attempted = False
        self._loans_attempted = False
        self._cas_landed = False
        self._ajax_succeeded = False

    def observe_entry_once(self) -> CasHttpObservation:
        with self._cas_lock:
            if self._entry_attempted:
                raise AjaxCasError("cas_entry_already_attempted")
            if self._closed:
                raise AjaxCasError("cas_client_closed")
            self._entry_attempted = True
        try:
            with self.http.stream("GET", ENTRY, follow_redirects=False,
                                  headers={"Accept": "text/html"}) as response:
                if response.status_code != 302:
                    raise AjaxCasError("cas_entry_unverified")
                raw = response.headers.get("location", "")
                if not raw:
                    raise AjaxCasError("cas_entry_redirect_refused")
                target = _url(urljoin(ENTRY, raw))
                if target.hostname != "mediatheques.agglo-larochelle.fr" or target.path != "/login":
                    raise AjaxCasError("cas_entry_redirect_refused")
                params = parse_qs(target.query, keep_blank_values=True, max_num_fields=8)
                if set(params) != {"service"} or len(params["service"]) != 1:
                    raise AjaxCasError("cas_entry_redirect_refused")
                service = _url(params["service"][0])
                if (service.hostname != "www.biblioaccess.com"
                        or service.path not in ("/1409", "/1409/")
                        or service.query):
                    raise AjaxCasError("cas_service_refused")
                self._service = params["service"][0]
                self._login_url = target.geturl()
                return CasHttpObservation("entry_redirect_unverified", 302)
        except (httpx.HTTPError, UnicodeError):
            raise AjaxCasError("cas_transport_unavailable") from None

    def observe_login_once(self) -> CasHttpObservation:
        """GET the institution's fresh service URL; no DOM or browser."""
        if self._closed:
            raise AjaxCasError("cas_client_closed")
        if not self._login_url or self._login_seen:
            raise AjaxCasError("cas_login_unavailable")
        self._login_seen = True
        try:
            with self.http.stream("GET", self._login_url,
                                  follow_redirects=False,
                                  headers={"Accept": "text/html"}) as response:
                if response.status_code != 200:
                    raise AjaxCasError("cas_login_unverified")
                media = response.headers.get("content-type", "").split(";", 1)[0].lower().strip()
                if media not in ("text/html", "application/xhtml+xml"):
                    raise AjaxCasError("cas_login_unverified")
                body = _bounded(response)
                self._preflight = inspect_login_preflight(
                    body.decode("utf-8", errors="strict"), page_url=self._login_url)
                return CasHttpObservation("login_html_unverified", 200)
        except (httpx.HTTPError, UnicodeError):
            raise AjaxCasError("cas_transport_unavailable") from None

    def login_preflight(self) -> LoginPreflight:
        """Return structural evidence from the one already-read login HTML."""
        if self._closed or self._preflight is None:
            raise AjaxCasError("cas_preflight_unavailable")
        return self._preflight

    def submit_once(self, *, query: dict[str, str], fields: dict[str, str],
                    consent: bool):
        # The AJAX client remains an explicit one-shot POST. The preflight
        # anchors the institution and tenant; it does not construct dynamic
        # parameters or bypass challenges.
        if self._service is None or not self._login_seen:
            raise AjaxCasError("cas_login_required")
        result = super().submit_once(query=query, fields=fields, consent=consent)
        self._ajax_succeeded = result.outcome == "success_reported"
        return result

    def observe_cas_once(self) -> CasHttpObservation:
        with self._cas_lock:
            if self._cas_attempted:
                raise AjaxCasError("cas_already_attempted")
            if self._closed:
                raise AjaxCasError("cas_client_closed")
            if not self._service or not self._ajax_succeeded:
                raise AjaxCasError("cas_post_not_confirmed")
            self._cas_attempted = True
        target = None
        try:
            with self.http.stream(
                "GET", CAS, params={"service": self._service},
                follow_redirects=False, headers={"Accept": "text/html"},
            ) as response:
                if response.status_code != 302:
                    raise AjaxCasError("cas_ticket_unverified")
                raw = response.headers.get("location", "")
                if not raw:
                    raise AjaxCasError("cas_ticket_refused")
                target = urljoin(CAS, raw)
                try:
                    dest = _url(target)
                except AjaxCasError:
                    raise AjaxCasError("cas_ticket_refused") from None
                params = parse_qs(dest.query, keep_blank_values=True, max_num_fields=8)
                svc = urlsplit(self._service)
                if (dest.hostname != svc.hostname or dest.path != svc.path
                        or set(params) != {"ticket"} or len(params["ticket"]) != 1
                        or not 1 <= len(params["ticket"][0]) <= 2048):
                    raise AjaxCasError("cas_ticket_refused")
            # One exact validated, fresh ticket landing on the provider.
            with self.http.stream("GET", target, follow_redirects=False,
                                  headers={"Accept": "text/html"}) as response:
                if response.status_code != 200:
                    raise AjaxCasError("cas_landing_unverified")
                media = response.headers.get("content-type", "").split(";", 1)[0].lower().strip()
                if media not in ("text/html", "application/xhtml+xml"):
                    raise AjaxCasError("cas_landing_unverified")
                _bounded(response)
                self._cas_landed = True
                return CasHttpObservation("landing_unverified", 200)
        except (httpx.HTTPError, UnicodeError, ValueError) as exc:
            if isinstance(exc, AjaxCasError):
                raise
            raise AjaxCasError("cas_transport_unavailable") from None

    def observe_loans_once(self) -> CasHttpObservation:
        with self._cas_lock:
            if self._loans_attempted:
                raise AjaxCasError("cas_loans_already_attempted")
            if self._closed:
                raise AjaxCasError("cas_client_closed")
            if not self._cas_landed:
                raise AjaxCasError("cas_landing_required")
            self._loans_attempted = True
        try:
            with self.http.stream("GET", LOANS, follow_redirects=False,
                                  headers={"Accept": "text/html"}) as response:
                if response.status_code != 200:
                    raise AjaxCasError("cas_loans_unverified")
                media = response.headers.get("content-type", "").split(";", 1)[0].lower().strip()
                if media not in ("text/html", "application/xhtml+xml"):
                    raise AjaxCasError("cas_loans_unverified")
                _bounded(response)
                return CasHttpObservation("loans_html_unverified", 200)
        except (httpx.HTTPError, UnicodeError):
            raise AjaxCasError("cas_transport_unavailable") from None

    def close(self):
        self._service = None
        self._login_url = None
        self._preflight = None
        self._ajax_succeeded = False
        self._cas_landed = False
        super().close()
