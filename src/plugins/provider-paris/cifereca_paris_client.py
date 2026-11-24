from __future__ import annotations

import logging
import hashlib
import contextvars
import json
import time
import uuid
import html
import ipaddress
import re
from html.parser import HTMLParser
from importlib import metadata
from dataclasses import asdict, replace
from typing import Any, Iterable, Callable
from urllib.parse import quote, urljoin, urlparse, unquote
import httpx
from cifereca.plugin_api import HTTPX_TRANSPORT
from cifereca.plugin_api import Account, HistoryItem, ItemDetail, Loan, SearchResult, AttentionRequired
from cifereca_paris_constants import ACCOUNT_SERVICE, BASE_URL, CHECK_AVAILABILITY_PATH, HOLDINGS_PATH, LOGIN_PATH, SEARCH_PATH
from cifereca_paris_trace import (
    public_book_id, public_content_type, public_http_status, public_site,
)
from cifereca_paris_normalize import normalize_history, normalize_item_detail, normalize_loan, normalize_search_result
from cifereca_paris_bibliography import project_search_response_facets

# A selected public HTTPS supplier must not fill memory with an unbounded
# body (including a 200 HTML/login/error response instead of an ACSM).
_MAX_PNB_PUBLIC_BODY_BYTES = 128 * 1024 * 1024

_BORROW_TRACE=contextvars.ContextVar('paris_borrow_trace',default='none')
class _ParisTrace(logging.LoggerAdapter):
    def process(self,msg,kwargs):return 'PNB trace='+_BORROW_TRACE.get()+' '+msg,kwargs
_TRACE_LOG = _ParisTrace(logging.getLogger("cifereca"),{})

class _NullDiagnostics:
    def add(self, *args: Any, **kwargs: Any) -> None: pass


class ParisError(RuntimeError):
    pass

class ParisContentTransferUnavailable(ParisError):
    """Temporary network failure of an attempted PNB GET."""


class ParisRedirectError(AttentionRequired, ParisError):
    """PNB navigation refused; retrying the same policy cannot resolve it."""

    def __init__(self, host: str) -> None:
        # The rejected hostname is untrusted. An opaque account/token can be
        # embedded in a subdomain; never copy arbitrary host text to public
        # exceptions, UI notices or diagnostics. Preserve recognized public
        # hostnames through the same closed classifier as HTTP traces.
        site = public_site("https://" + host + "/") if type(host) is str and host else "sans hôte"
        super().__init__(
            f"Redirection PNB refusée vers un site non autorisé : {site}. "
            "Vérifiez la mise à jour du plugin Paris ; une nouvelle tentative seule ne résout pas ce refus.",
            code="pnb_redirect_refused",
        )


class ParisSearchResponseInvalid(ParisError):
    """SearchService returned no trustworthy result-page envelope."""


class ParisSearchUnavailable(ParisError):
    code = 'search_temporarily_unavailable'
    retryable = True


PARIS_LOGIN_REASON_CODES = frozenset({
    "paris_login_http_forbidden",
    "paris_login_http_unauthorized",
    "paris_login_rate_limited",
    "paris_login_upstream_unavailable",
    "paris_login_rejected",
})


class AuthenticationError(ParisError):
    """Private human message and separately trusted, closed diagnostic code."""

    def __init__(self, message: str, *, code: str = "paris_login_rejected") -> None:
        if code not in PARIS_LOGIN_REASON_CODES:
            raise ValueError("invalid_paris_login_reason")
        super().__init__(message)
        self.code = code

PARIS_SUMMARY_HTTP_CODES = frozenset({
    "paris_summary_http_forbidden", "paris_summary_http_unauthorized",
    "paris_summary_rate_limited", "paris_summary_upstream_unavailable",
})


class ParisSummaryHTTPError(ParisError):
    """Closed reason for a failed account-summary HTTP response."""

    def __init__(self, code: str) -> None:
        if code not in PARIS_SUMMARY_HTTP_CODES:
            raise ValueError("invalid_paris_summary_http_reason")
        super().__init__("Compte Paris : réponse HTTP indisponible")
        self.code = code



# Bounded evidence for a ListLoans *read*. Do not derive loan absence from HTTP
# errors, redirects, non-JSON bodies or a successful sibling branch (#3222).
PARIS_LOAN_ERROR_CODES = frozenset({
    "paris_loans_http_not_found",
    "paris_loans_http_unauthorized",
    "paris_loans_http_forbidden",
    "paris_loans_http_rate_limited",
    "paris_loans_http_unavailable",
    "paris_loans_http_other",
    "paris_loans_redirect_unexpected",
    "paris_loans_html_unexpected",
    "paris_loans_non_json",
    "paris_loans_json_shape_invalid",
    "paris_loans_transport_timeout",
    "paris_loans_transport_failed",
    "paris_loans_pagination_incomplete",
})


class ParisLoanListError(ParisError):
    """Typed ListLoans read failure, never a positive/empty inventory."""

    def __init__(self, code: str, *, status: int | None = None):
        if code not in PARIS_LOAN_ERROR_CODES:
            raise ValueError("paris_loans_reason_invalid")
        super().__init__(code)
        self.code = code
        self.http_status = public_http_status(status)


def _list_loans_response_error(response: httpx.Response) -> str | None:
    status = response.status_code
    if status == 404:
        return "paris_loans_http_not_found"
    if status == 401:
        return "paris_loans_http_unauthorized"
    if status == 403:
        return "paris_loans_http_forbidden"
    if status == 429:
        return "paris_loans_http_rate_limited"
    if 500 <= status <= 599:
        return "paris_loans_http_unavailable"
    if 300 <= status <= 399:
        return "paris_loans_redirect_unexpected"
    if not response.is_success:
        return "paris_loans_http_other"
    return None


from cifereca_paris_catalogue import ParisCatalogueOperations


from cifereca_paris_pnb import ParisPnbOperations


class ParisClient(ParisCatalogueOperations, ParisPnbOperations):
    def __init__(self, base_url: str = BASE_URL, timeout: float = 25.0, diagnostics: Any | None = None,
                 client_factory: Callable[[], httpx.Client] | None = None):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.diagnostics = diagnostics or _NullDiagnostics()
        self._client_factory = client_factory
        self._http_profile = "browser"
        self.client = self._create_http_client(self._http_profile)
        self.authenticated = False
        self.username: str | None = None
        self.last_search_warnings: list[str] = []
        # SearchService facets belong to a single response page, never a
        # SearchResult or a proved server filtering capability.
        self.last_search_response_facets: list[dict[str, Any]] = []
        self.last_pairing_errors: list[dict[str, str]] = []
        self.last_history_errors: list[dict[str, str]] = []
        self.last_pnb_borrow: dict[str, Any] = {"phase": "idle"}

    def _create_http_client(self, profile: str = "browser") -> httpx.Client:
        if self._client_factory is not None:
            return self._client_factory()
        headers = {
            "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.7",
        }
        limits = httpx.Limits(max_connections=20, max_keepalive_connections=10, keepalive_expiry=20.0)
        if profile == "browser":
            # Le script HTTP local qui a validé l'architecture s'exécute avec
            # Brotli disponible. On rend ici ce profil explicite afin que la
            # virtualenv n'envoie pas une requête différente par inadvertance.
            headers["Accept-Encoding"] = "gzip, deflate, br"
        elif profile == "compat":
            # Fallback unique pour certains reverse proxies qui ferment une
            # réponse avant son body : pas de compression et pas de keep-alive.
            headers["Accept-Encoding"] = "identity"
            headers["Connection"] = "close"
            limits = httpx.Limits(max_connections=10, max_keepalive_connections=0)
        return HTTPX_TRANSPORT.client(producer="provider.paris",
            headers=headers,
            follow_redirects=True,
            timeout=httpx.Timeout(self.timeout),
            limits=limits,
            http1=True,
            http2=False,
        )

    def _reset_http_client(self, profile: str | None = None) -> None:
        """Abandonne une connexion potentiellement corrompue et repart avec un cookie jar vide."""
        try:
            self.client.close()
        except Exception:
            pass
        if profile is not None:
            self._http_profile = profile
        self.client = self._create_http_client(self._http_profile)
        self.authenticated = False

    def _log_http_stack(self) -> None:
        def version(name: str) -> str:
            try:
                return metadata.version(name)
            except Exception:
                return "unknown"
        try:
            import brotli  # noqa: F401
            brotli_available = True
        except Exception:
            brotli_available = False
        self.diagnostics.add(
            "HTTP", "stack", "info",
            detail={
                "profile": self._http_profile,
                "httpx": version("httpx"),
                "httpcore": version("httpcore"),
                "brotli": brotli_available,
                "accept_encoding": self.client.headers.get("Accept-Encoding", ""),
                "http2": False,
                "bootstrap": "skipped",
            },
            level="detailed",
        )

    def close(self) -> None:
        self.client.close()

    def _api_headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.7",
            "Referer": f"{self.base_url}/numerique/account.aspx",
            "X-Requested-With": "XMLHttpRequest",
        }

    def _params(self, provider: str, uid: str = "") -> dict[str, str]:
        now = str(int(time.time() * 1000))
        return {"serviceCode": provider, "token": now, "userUniqueIdentifier": uid, "timestamp": now}

    @staticmethod
    def _error_text(value: Any) -> str | None:
        """Transforme les erreurs Syracuse structurées en texte utilisateur lisible."""
        if value is None:
            return None
        if isinstance(value, str):
            text = value.strip()
            return text or None
        if isinstance(value, dict):
            for key in ("msg", "message", "Message", "description", "id"):
                candidate = value.get(key)
                if isinstance(candidate, str) and candidate.strip():
                    return candidate.strip()
            return None
        return str(value)

    def _json(self, response: httpx.Response, operation: str, require_success: bool = True) -> dict[str, Any]:
        try:
            data = response.json()
        except Exception as exc:
            raise ParisError(f"{operation}: réponse non JSON (HTTP {response.status_code})") from exc
        if not isinstance(data, dict):
            raise ParisError(f"{operation}: JSON inattendu")
        if require_success and (not response.is_success or data.get("success") is not True):
            errors = data.get("errors")
            first_error = errors[0] if isinstance(errors, list) and errors else None
            message = self._error_text(data.get("message")) or self._error_text(first_error) or f"HTTP {response.status_code}"
            raise ParisError(f"{operation}: {message}")
        return data

    def _timed(self, category: str, operation: str, fn):
        start = time.perf_counter()
        try:
            result = fn()
            self.diagnostics.add(category, operation, "ok", int((time.perf_counter()-start)*1000))
            return result
        except Exception as exc:
            if category=="PNB" and operation=="borrow digital":
                # The existing canonical local TRACE event must retain the
                # *original* provider exception and traceback. A type-only
                # summary discards the reason a real PNB action failed.
                # Diagnostics UI pseudonymization belongs to Core, not here.
                _TRACE_LOG.warning(
                    "TRACE paris pnb-loan failed error=%s duration_ms=%s",
                    type(exc).__name__, int((time.perf_counter()-start)*1000),
                    exc_info=True,
                )
            detail = {"error": type(exc).__name__}
            # Keep the historical policy of unrelated operations unchanged.
            # ListLoans messages are actual provider facts and must not be
            # suppressed by the privacy mask introduced by #3413 (#3423).
            private_operation = (
                category == "AUTH"
                or (category == "API" and operation.startswith("summary "))
                or (category == "CATALOG" and (
                    operation.startswith("search ")
                    or operation.startswith("history cover search ")
                ))
            )
            if category == "AUTH" and isinstance(exc, AuthenticationError):
                detail["code"] = exc.code
            if (category == "API" and operation.startswith("summary ")
                    and isinstance(exc, ParisSummaryHTTPError)):
                detail["code"] = exc.code
            if (category == "API" and operation.startswith("loans ")):
                if isinstance(exc, ParisLoanListError):
                    detail["code"] = exc.code
                    detail["http_status"] = exc.http_status
                elif isinstance(exc, httpx.TimeoutException):
                    detail["code"] = "paris_loans_transport_timeout"
                elif isinstance(exc, httpx.RequestError):
                    detail["code"] = "paris_loans_transport_failed"
            if category == "API" and operation.startswith("loans "):
                # The exact observed ListLoans exception text belongs to the
                # raw journal and broker, including text beyond 240 chars.
                # It complements the structured reason and HTTP status.
                detail["message"] = str(exc)
            elif not private_operation:
                detail["message"] = str(exc)[:240]
            self.diagnostics.add(category, operation, "error", int((time.perf_counter()-start)*1000), detail)
            raise

    def bootstrap(self) -> int:
        return self._timed("HTTP", "bootstrap", lambda: self.client.get(f"{self.base_url}/numerique/").status_code)

    def login(self, username: str, password: str) -> dict[str, Any]:
        def post_login() -> dict[str, Any]:
            headers = self._api_headers()
            headers.update({
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "Origin": self.base_url,
                "Referer": f"{self.base_url}/numerique/",
            })
            r = self.client.post(
                f"{self.base_url}{LOGIN_PATH}",
                headers=headers,
                data={"username": username, "password": password, "rememberMe": "false"},
            )
            if r.status_code == 403:
                raise AuthenticationError(
                    "Paris refuse le login HTTP direct depuis cette machine (403)",
                    code="paris_login_http_forbidden",
                )
            if r.status_code == 401:
                raise AuthenticationError(
                    "Paris refuse l'authentification (401)",
                    code="paris_login_http_unauthorized",
                )
            if r.status_code == 429:
                raise AuthenticationError(
                    "Paris limite temporairement les connexions (429)",
                    code="paris_login_rate_limited",
                )
            if 500 <= r.status_code <= 599:
                raise AuthenticationError(
                    "Service d'authentification Paris indisponible",
                    code="paris_login_upstream_unavailable",
                )
            data = self._json(r, "login", require_success=False)
            if not r.is_success or data.get("success") is not True:
                raise AuthenticationError(str(data.get("message") or "Identifiant ou mot de passe incorrect"))
            self.authenticated = True
            self.username = username
            return data

        def work():
            # Le bootstrap n'est pas requis : le POST logon crée lui-même les
            # cookies Syracuse. Le supprimer évite un aller-retour fragile et
            # aligne le chemin critique sur le strict minimum.
            if self._http_profile != "browser":
                self._reset_http_client("browser")
            else:
                try:
                    self.client.cookies.clear()
                except Exception:
                    pass
            self._http_profile = "browser"
            self._log_http_stack()
            for attempt in (1, 2):
                try:
                    return post_login()
                except httpx.RemoteProtocolError as exc:
                    if attempt >= 2:
                        self.diagnostics.add(
                            "AUTH", "login retry exhausted", "error",
                            detail={
                                "attempt": "2/2",
                                "profile": self._http_profile,
                                "error": type(exc).__name__,
                                "code": "paris_login_retry_exhausted",
                            },
                        )
                        raise
                    self.diagnostics.add(
                        "AUTH", "login retry", "retry",
                        detail={
                            "attempt": "1/2",
                            "profile": self._http_profile,
                            "error": type(exc).__name__,
                            "code": "paris_login_protocol_retry",
                            "action": "retry_compat_identity_close",
                        },
                    )
                    self._reset_http_client("compat")
                    self._log_http_stack()
                    time.sleep(0.35)
            raise AssertionError("unreachable")

        return self._timed("AUTH", "login", work)

    def summary(self, provider: str = "SYRACUSE", uid: str = "") -> dict[str, Any]:
        def work():
            r = self.client.get(f"{self.base_url}{ACCOUNT_SERVICE}/RetrieveAccountSummary", params=self._params(provider, uid), headers=self._api_headers())
            # Distinguish server/WAF refusals from disabled credentials without
            # logging any upstream response or signed endpoint URL.
            reason = (
                "paris_summary_http_forbidden" if r.status_code == 403 else
                "paris_summary_http_unauthorized" if r.status_code == 401 else
                "paris_summary_rate_limited" if r.status_code == 429 else
                "paris_summary_upstream_unavailable" if 500 <= r.status_code <= 599
                else None
            )
            if reason is not None:
                raise ParisSummaryHTTPError(reason)
            data = self._json(r, f"summary {provider}")
            d = data.get("d") if isinstance(data.get("d"), dict) else {}
            return d.get("AccountSummary") if isinstance(d.get("AccountSummary"), dict) else d
        return self._timed("API", f"summary {provider}", work)

    def loans(self, provider: str, uid: str = "") -> list[Loan]:
        def work():
            r = self.client.get(f"{self.base_url}{ACCOUNT_SERVICE}/ListLoans", params=self._params(provider, uid), headers=self._api_headers())
            upstream = provider if provider in ("DILICOM", "SYRACUSE") else "unknown"
            content_type = r.headers.get("content-type")
            # Log the exact observed Content-Type instead of silently
            # dropping parameters; do not invent a response content type or
            # issue another request. Never log a synthetic HTTP exchange.
            _TRACE_LOG.info(
                "TRACE paris list-loans phase=response method=GET route=ListLoans upstream=%s http_status=%s content_type=%s",
                upstream, r.status_code, content_type,
            )
            # A followed 302/307 must not turn into a successful loan JSON.
            # Only the original ListLoans response is admissible as inventory.
            if r.history:
                raise ParisLoanListError("paris_loans_redirect_unexpected",
                                         status=r.status_code)
            code = _list_loans_response_error(r)
            if code is not None:
                raise ParisLoanListError(code, status=r.status_code)
            if public_content_type(content_type) == "text/html":
                raise ParisLoanListError(
                    "paris_loans_redirect_unexpected" if r.history
                    else "paris_loans_html_unexpected",
                    status=r.status_code,
                )
            try:
                data = r.json()
            except (ValueError, TypeError):
                raise ParisLoanListError("paris_loans_non_json", status=r.status_code) from None
            if type(data) is not dict:
                raise ParisLoanListError("paris_loans_json_shape_invalid", status=r.status_code)
            if data.get("success") is not True:
                # Preserve the existing structured upstream-error contract.
                self._json(r, f"loans {provider}")
            # A malformed/partial ListLoans response is NOT evidence of zero
            # loans. Preserve earlier rights instead of silently dropping rows.
            envelope = data.get("d")
            if type(envelope) is not dict or "Loans" not in envelope:
                raise ParisError("paris_loans_inventory_shape_invalid")
            rows = envelope["Loans"]
            if type(rows) is not list or any(type(row) is not dict for row in rows):
                raise ParisError("paris_loans_inventory_shape_invalid")
            # The active-loan list is authoritative only after the actual
            # read finishes. Do not promote an explicitly unfinished page to
            # complete rights coverage (including an empty first page).
            # This reader has no attested continuation protocol: refuse the
            # branch instead of guessing another URL or replaying a request.
            for paging in (data, envelope):
                unfinished = any(
                    key in paging and paging[key] is not None
                    and paging[key] is not False
                    for key in ("HasMore", "HasMorePages")
                ) or any(
                    key in paging and paging[key] not in (None, "", 0)
                    for key in ("NextPage", "Next", "ContinuationToken")
                )
                total = paging.get("TotalCount")
                if (unfinished or ("TotalCount" in paging and (
                        type(total) is not int or total < 0 or total != len(rows)))):
                    raise ParisLoanListError("paris_loans_pagination_incomplete",
                                             status=r.status_code)
            return [normalize_loan(row, provider) for row in rows]
        return self._timed("API", f"loans {provider}", work)

    def all_loans(self, uid: str = "") -> list[Loan]:
        rows = self.loans("DILICOM", uid) + self.loans("SYRACUSE", uid)
        return sorted(rows, key=lambda x: (x.due_at is None, x.due_at or "", x.title.casefold()))

    def history(self, provider: str, uid: str = "") -> list[HistoryItem]:
        def work():
            r = self.client.get(f"{self.base_url}{ACCOUNT_SERVICE}/ListHandings", params=self._params(provider, uid), headers=self._api_headers())
            data = self._json(r, f"history {provider}")
            rows = ((data.get("d") or {}).get("Handings") or [])
            return [normalize_history(x, provider) for x in rows if isinstance(x, dict)]
        return self._timed("API", f"history {provider}", work)

    def _aggregate_account_rows(self, operation: str, loader, uid: str) -> list[Any]:
        rows: list[Any] = []
        errors: list[dict[str, str]] = []
        for provider in ("DILICOM", "SYRACUSE"):
            try:
                rows.extend(loader(provider, uid))
            except Exception as exc:
                errors.append({"upstream": provider, "type": type(exc).__name__})
        if operation == "history":
            self.last_history_errors = errors
        if len(errors) == 2 and not rows:
            kinds = ",".join(sorted({row["type"] for row in errors}))
            raise ParisError(f"{operation} indisponible: {kinds}")
        return rows

    def all_history(self, uid: str = "") -> list[HistoryItem]:
        self.last_history_errors = []
        rows = self._aggregate_account_rows("history", self.history, uid)
        return sorted(rows, key=lambda x: x.borrowed_at or x.returned_at or "", reverse=True)

    def pairings(self) -> list[Account]:
        def work():
            seen: dict[str, Account] = {}
            errors: list[dict[str, str]] = []
            for provider in ("SYRACUSE", "DILICOM"):
                try:
                    now = str(int(time.time() * 1000))
                    r = self.client.get(
                        f"{self.base_url}{ACCOUNT_SERVICE}/ListUserPairings",
                        params={"serviceCode": provider, "timestamp": now},
                        headers=self._api_headers(),
                    )
                    data = self._json(r, f"pairings {provider}")
                    d = data.get("d") if isinstance(data.get("d"), dict) else {}
                    for bucket in ("UserPairings", "UserReversePairings"):
                        for raw in d.get(bucket) or []:
                            if not isinstance(raw, dict) or int(raw.get("State") or 0) != 2:
                                continue
                            uid = str(raw.get("UserUniqueIdentifier") or "")
                            if uid and uid not in seen:
                                seen[uid] = Account(
                                    display_name=str(raw.get("UserDisplayName") or "Compte lié"),
                                    barcode=str(raw.get("UserBarcode") or ""),
                                    unique_id=uid,
                                    is_primary=False,
                                )
                except Exception as exc:
                    error = {"upstream": provider, "type": type(exc).__name__}
                    errors.append(error)
                    try:
                        self.diagnostics.add(
                            "API",
                            f"pairings {provider}",
                            "error",
                            detail=error,
                        )
                    except Exception:
                        pass
            self.last_pairing_errors = errors
            if len(errors) == 2 and not seen:
                kinds = ",".join(sorted({row["type"] for row in errors}))
                raise ParisError(f"pairings indisponibles: {kinds}")
            return list(seen.values())
        self.last_pairing_errors = []
        return self._timed("API", "pairings", work)

    def check_availability_batch(self, records: list[dict[str, Any]], search_query: dict[str, Any]) -> list[Any]:
        def work():
            r = self.client.post(
                f"{self.base_url}{CHECK_AVAILABILITY_PATH}",
                headers={**self._api_headers(), "Content-Type": "application/json; charset=UTF-8", "Origin": self.base_url},
                json={"RecordIdArray": records, "searchQuery": search_query},
            )
            data = self._json(r, "check availability")
            return data.get("d") if isinstance(data.get("d"), list) else []
        return self._timed("CATALOG", "check availability batch", work)
    def renew_loan(self, loan: Loan, uid: str = "") -> dict[str, Any]:
        # #4047: direct ParisClient callers must not bypass the physical-only
        # admission performed by the Core and by ParisProviderSession.
        if (type(loan) is not Loan or loan.source_provider != "paris"
                or loan.medium != "physical" or loan.provider != "SYRACUSE"
                or loan.renewable is not True or type(loan.raw) is not dict
                or loan.raw.get("CanRenew") is not True
                or (loan.raw.get("Id") or loan.raw.get("HoldingId")) != loan.id
                or type(loan.id) is not str or not loan.id
                or (loan.state is not None and (
                    type(loan.state) is not str or loan.state.casefold() in {
                        "returned", "closed", "expired", "cancelled", "canceled",
                    }
                ))):
            raise ParisError("Prolongation réservée aux prêts physiques Paris éligibles")
        def work():
            r = self.client.post(
                f"{self.base_url}{ACCOUNT_SERVICE}/RenewLoans",
                headers={**self._api_headers(), "Content-Type": "application/json; charset=UTF-8"},
                json={"loans": [loan.raw], "userUniqueIdentifier": uid},
            )
            return self._json(r, "renew loan")
        return self._timed("ACTION", "renew loan", work)
