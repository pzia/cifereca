"""Read-only CAS continuation for the observed institutional AJAX logon.

No browser and no guessed POST fields. The AJAX POST itself is implemented in
biblioaccess_ajax_logon; this wrapper reuses the SAME HTTPX cookie jar, pins
the CAS service from the provider's fresh login redirect, and never reports
Core authentication or a library right. No networking at import time.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock
from urllib.parse import parse_qs, quote, urljoin, urlsplit

import httpx

from biblioaccess_ajax_logon import ObservedAjaxLogonClient, AjaxLogonError
from biblioaccess_ajax_preflight import (
    inspect_login_preflight, inspect_javascript_evidence,
    LoginPreflight, JavascriptEvidence,
)

BIBLIO = "https://www.biblioaccess.com"
IDP = "https://mediatheques.agglo-larochelle.fr"
# Exact navigation target observed when the user clicks BiblioAccess login.
# returnUrl is a navigation parameter, not a CAS service ticket.
RETURN_URL = BIBLIO + "/1409/"
ENTRY = (BIBLIO + "/1409/Account/Login?returnUrl="
         + quote(RETURN_URL, safe=""))
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
    page_book_link_count: int = 0
    page_book_unique_count: int = 0
    inventory_verified: bool = False
    _candidate_book_urls: tuple[tuple[str, str], ...] = field(
        default=(), repr=False, compare=False,
    )


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
        self._login_attempted = False
        self._login_qualified = False
        self._preflight = None
        self._script_attempted = False
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
        """One GET, qualified only after a complete bounded login HTML read.

        A failed preflight (302, 403, truncated body or transport failure)
        permanently consumes this client's login attempt and cannot enable
        the credential-bearing AJAX POST.
        """
        with self._cas_lock:
            if self._closed:
                raise AjaxCasError("cas_client_closed")
            if not self._login_url or self._login_attempted:
                raise AjaxCasError("cas_login_unavailable")
            self._login_attempted = True
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
                preflight = inspect_login_preflight(
                    body.decode("utf-8", errors="strict"), page_url=self._login_url)
            with self._cas_lock:
                if self._closed:
                    raise AjaxCasError("cas_client_closed")
                self._preflight = preflight
                self._login_qualified = True
            return CasHttpObservation("login_html_unverified", 200)
        except (httpx.HTTPError, UnicodeError):
            raise AjaxCasError("cas_transport_unavailable") from None

    def login_preflight(self) -> LoginPreflight:
        """Return structural evidence from the one already-read login HTML."""
        if self._closed or self._preflight is None:
            raise AjaxCasError("cas_preflight_unavailable")
        return self._preflight

    def inspect_login_script_once(
        self, *, path: str, consent: bool,
    ) -> JavascriptEvidence:
        """Explicit single GET of an observed, same-origin JS (no execution).

        Only a path already present in the parsed login HTML is admissible.
        No query values, redirects or script bytes escape the client.
        """
        if consent is not True:
            raise AjaxCasError("cas_script_consent_required")
        with self._cas_lock:
            if self._closed:
                raise AjaxCasError("cas_client_closed")
            if self._script_attempted:
                raise AjaxCasError("cas_script_already_attempted")
            if (self._preflight is None or type(path) is not str
                    or path not in self._preflight.same_origin_script_paths):
                raise AjaxCasError("cas_script_unobserved")
            # The selected path maps to exactly one freshly observed URL.
            # Its query is deliberately ephemeral and never returned/logged.
            sources = dict(self._preflight._script_request_urls)
            observed_url = sources.get(path)
            if observed_url is None:
                raise AjaxCasError("cas_script_unobserved")
            self._script_attempted = True
        try:
            with self.http.stream(
                "GET", observed_url, follow_redirects=False,
                headers={"Accept": "application/javascript, text/javascript"},
            ) as response:
                if response.status_code != 200:
                    raise AjaxCasError("cas_script_unavailable")
                media = response.headers.get("content-type", "").split(";", 1)[0].lower().strip()
                if media not in ("text/javascript", "application/javascript",
                                 "application/x-javascript"):
                    raise AjaxCasError("cas_script_unexpected_content")
                return inspect_javascript_evidence(
                    _bounded(response, 128 * 1024).decode("utf-8", errors="strict"))
        except (httpx.HTTPError, UnicodeError):
            raise AjaxCasError("cas_script_transport_unavailable") from None

    def submit_once(self, *, query: dict[str, str], fields: dict[str, str],
                    consent: bool):
        # The AJAX client remains an explicit one-shot POST. The preflight
        # anchors the institution and tenant; it does not construct dynamic
        # parameters or bypass challenges.
        with self._cas_lock:
            if self._closed or self._service is None or not self._login_qualified:
                raise AjaxCasError("cas_login_required")
        result = super().submit_once(query=query, fields=fields, consent=consent)
        with self._cas_lock:
            self._ajax_succeeded = (
                not self._closed and result.outcome == "success_reported"
            )
        return result

    def submit_observed_login_once(
        self, *, username: str, password: str, consent: bool,
    ):
        """One consented ordinary AJAX login with fresh HTML-derived values.

        No screenshot, JS execution, CAPTCHA solving or manual token entry.
        Never use this path when the HTML already requires a CAPTCHA.
        """
        from time import time_ns
        if consent is not True:
            raise AjaxCasError("cas_login_consent_required")
        with self._cas_lock:
            if self._closed or not self._login_qualified or self._preflight is None:
                raise AjaxCasError("cas_login_required")
            context = self._preflight._login_post_context
            if context is None:
                raise AjaxCasError("cas_login_dynamic_inputs_unavailable")
            provider, action, check_value, captcha, remember = context
            if provider != "Form" or action != "logon":
                raise AjaxCasError("cas_login_dynamic_inputs_unavailable")
        query = {
            "changeUser": "true",
            "provider": provider,
            "_dc": str(time_ns() // 1_000_000),
        }
        fields = {
            "username": username,
            "password": password,
            "rememberMe": "true" if remember else "false",
            "captcha": captcha,
            "captchaCheckValue": check_value,
        }
        return self.submit_once(query=query, fields=fields, consent=True)

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
                body = _bounded(response)
                from biblioaccess_loans_page import (
                    inspect_myloans_html, LoanPageError,
                )
                try:
                    candidates = inspect_myloans_html(
                        body.decode("utf-8", errors="strict"))
                except LoanPageError:
                    raise AjaxCasError("cas_loans_structure_unverified") from None
                return CasHttpObservation(
                    "loans_html_unverified", 200,
                    page_book_link_count=candidates.page_book_link_count,
                    page_book_unique_count=candidates.page_book_unique_count,
                    _candidate_book_urls=candidates._book_urls,
                )
        except (httpx.HTTPError, UnicodeError):
            raise AjaxCasError("cas_transport_unavailable") from None

    def close(self):
        self._service = None
        self._login_url = None
        self._preflight = None
        self._login_qualified = False
        self._ajax_succeeded = False
        self._cas_landed = False
        super().close()
