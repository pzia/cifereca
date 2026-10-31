from __future__ import annotations

import logging
import hashlib
import contextvars
import json
import time
import uuid
import html
import re
from html.parser import HTMLParser
from importlib import metadata
from dataclasses import asdict
from typing import Any, Iterable, Callable
from urllib.parse import quote, urljoin, urlparse, unquote
import httpx
from cifereca.plugin_api import HTTPX_TRANSPORT
from cifereca.plugin_api import Account, Booking, HistoryItem, ItemDetail, Loan, SearchResult, AttentionRequired
from cifereca_paris_constants import ACCOUNT_SERVICE, BASE_URL, CHECK_AVAILABILITY_PATH, HOLDINGS_PATH, LOGIN_PATH, SEARCH_PATH
from cifereca_paris_trace import (
    public_book_id, public_content_type, public_http_status, public_site,
)
from cifereca_paris_normalize import normalize_booking, normalize_history, normalize_item_detail, normalize_loan, normalize_search_result
from cifereca_paris_bibliography import project_search_response_facets

_BORROW_TRACE=contextvars.ContextVar('paris_borrow_trace',default='none')
class _ParisTrace(logging.LoggerAdapter):
    def process(self,msg,kwargs):return 'PNB trace='+_BORROW_TRACE.get()+' '+msg,kwargs
_TRACE_LOG = _ParisTrace(logging.getLogger("cifereca"),{})

class _NullDiagnostics:
    def add(self, *args: Any, **kwargs: Any) -> None: pass


class ParisError(RuntimeError):
    pass

class ParisRedirectError(AttentionRequired, ParisError):
    """PNB navigation refused; retrying the same policy cannot resolve it."""

    def __init__(self, host: str) -> None:
        super().__init__(
            f"Redirection PNB refusée vers un hôte non autorisé : {host or 'sans hôte'}. "
            "Vérifiez la mise à jour du plugin Paris ; une nouvelle tentative seule ne résout pas ce refus.",
            code="pnb_redirect_refused",
        )


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


class ParisClient:
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
        self._last_scenario_facet_observation: tuple[str, int, list[dict[str, Any]]] | None = None
        self.last_pairing_errors: list[dict[str, str]] = []
        self.last_history_errors: list[dict[str, str]] = []
        self.last_booking_errors: list[dict[str, str]] = []
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
                # An exception from a signed loan action can embed the
                # ACSM URL, account, cookie or full response body. Keep only
                # a bounded phase, closed status, and exception class.
                _TRACE_LOG.warning(
                    "TRACE paris pnb-loan failed error=%s duration_ms=%s",
                    type(exc).__name__, int((time.perf_counter()-start)*1000),
                    exc_info=False,
                )
            detail = {"error": type(exc).__name__}
            # Provider exception messages are untrusted; they may contain
            # customer-specific signed URLs even when the API returns HTTP 200.
            # Leave unrelated operation policies unchanged in this increment.
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
                # Closed transport/upstream code and HTTP status remain
                # searchable without storing raw borrower/signed URLs.
                detail["message"] = detail.get("code", "paris_loans_operation_failed")
            elif category == "PNB":
                detail["code"] = "paris_pnb_operation_failed"
                detail["message"] = detail["code"]
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
        elif operation == "bookings":
            self.last_booking_errors = errors
        if len(errors) == 2 and not rows:
            kinds = ",".join(sorted({row["type"] for row in errors}))
            raise ParisError(f"{operation} indisponible: {kinds}")
        return rows

    def all_history(self, uid: str = "") -> list[HistoryItem]:
        self.last_history_errors = []
        rows = self._aggregate_account_rows("history", self.history, uid)
        return sorted(rows, key=lambda x: x.borrowed_at or x.returned_at or "", reverse=True)

    def bookings(self, provider: str, uid: str = "") -> list[Booking]:
        def work():
            r = self.client.get(f"{self.base_url}{ACCOUNT_SERVICE}/ListBookings", params=self._params(provider, uid), headers=self._api_headers())
            data = self._json(r, f"bookings {provider}")
            rows = ((data.get("d") or {}).get("Bookings") or [])
            return [normalize_booking(x, provider) for x in rows if isinstance(x, dict)]
        return self._timed("API", f"bookings {provider}", work)

    def all_bookings(self, uid: str = "") -> list[Booking]:
        self.last_booking_errors = []
        return self._aggregate_account_rows("bookings", self.bookings, uid)

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

    def _search_query(self, query: str, scenario: str, page: int, result_size: int = 50) -> dict[str, Any]:
        encoded = quote(query)
        return {
            "Page": page, "ResultSize": result_size, "QueryString": query, "SearchLabel": "",
            "ScenarioCode": scenario, "InitialSearch": True, "SearchContext": 0, "PageRange": 3,
            "Url": f"{self.base_url}/search.aspx?SC={scenario}&QUERY={encoded}&QUERY_LABEL=#/Search/(query:(InitialSearch:!t,Page:{page},PageRange:3,QueryString:'{encoded}',ResultSize:{result_size},ScenarioCode:{scenario},SearchContext:0,SearchLabel:''))",
        }

    def _search_scenario(self, query: str, scenario: str, page: int, result_size: int) -> list[SearchResult]:
        # Clear before every network attempt, including failed fallback paths.
        self._last_scenario_facet_observation = None
        body = {"query": self._search_query(query, scenario, page, result_size), "sst": 4}
        r = self.client.post(
            f"{self.base_url}{SEARCH_PATH}",
            headers={**self._api_headers(), "Content-Type": "application/json; charset=UTF-8", "Origin": self.base_url, "Referer": f"{self.base_url}/search.aspx?SC={scenario}"},
            json=body,
        )
        # SearchService's 401/403 is an access verdict, not a wide-query
        # result-size problem. 408/429/5xx are HTTP outages: a smaller POST
        # to the same route does not repair them. Keep business responses
        # (including the documented broad-query error) on the existing
        # controlled 50 -> 20 retry path.
        if r.status_code in {401, 403, 408, 429} or r.status_code >= 500:
            r.raise_for_status()
        data = self._json(r, f"search {scenario}")
        self._last_scenario_facet_observation = (
            scenario, page, project_search_response_facets(data, scenario, page)
        )
        rows = ((data.get("d") or {}).get("Results") or [])
        return [x for x in (normalize_search_result(raw, scenario) for raw in rows if isinstance(raw, dict)) if x]

    def search_catalog(self, query: str, scenario: str = "CATALOGUE", page: int = 0, result_size: int = 20) -> list[SearchResult]:
        """Recherche un seul catalogue sans modifier l'état de recherche UI.

        Utilisé notamment pour enrichir à la demande les couvertures d'historique
        d'un profil lié. La méthode reste bornée afin de ne jamais transformer ce
        fallback en moissonnage du catalogue.
        """
        scenario = str(scenario or "CATALOGUE").upper()
        if scenario not in {"CATALOGUE", "EBOOKS"}:
            raise ValueError(f"Scénario de recherche inconnu: {scenario}")
        size = max(1, min(int(result_size), 50))
        return self._timed(
            "CATALOG", f"history cover search {scenario}",
            lambda: self._search_scenario(query, scenario, page, size),
        )

    def search(self, query: str, scope: str = "all", page: int = 0) -> list[SearchResult]:
        """Recherche tolérante aux défaillances d'un des deux catalogues.

        Les requêtes très larges pouvaient demander ``ResultSize=-1`` et provoquer
        l'erreur métier SearchService « Votre recherche a échoué ». On borne
        désormais la page à 50 résultats/scénario, avec un retry plus conservateur
        à 20 résultats. En mode ``all``, une panne EBOOKS ne masque jamais les
        résultats physiques déjà récupérés.
        """
        scenarios = ["EBOOKS"] if scope == "digital" else ["CATALOGUE"] if scope == "physical" else ["CATALOGUE", "EBOOKS"]
        out: list[SearchResult] = []
        seen: set[str] = set()
        self.last_search_warnings = []
        self.last_search_response_facets = []
        failures: list[tuple[str, Exception]] = []
        successful_scenarios = 0

        for scenario in scenarios:
            rows: list[SearchResult] | None = None
            self._last_scenario_facet_observation = None
            try:
                rows = self._timed(
                    "CATALOG", f"search {scenario}",
                    lambda scenario=scenario: self._search_scenario(query, scenario, page, 50),
                )
            except ParisError as first_exc:
                # Retry borné : utile surtout pour EBOOKS sur les recherches larges.
                self.diagnostics.add(
                    "CATALOG", f"search {scenario} fallback", "retry",
                    detail={"result_size": 20, "error": type(first_exc).__name__},
                    level="detailed",
                )
                try:
                    self._last_scenario_facet_observation = None
                    rows = self._timed(
                        "CATALOG", f"search {scenario} fallback",
                        lambda scenario=scenario: self._search_scenario(query, scenario, page, 20),
                    )
                except Exception as second_exc:
                    failures.append((scenario, second_exc))
                    self.last_search_warnings.append(
                        "Catalogue numérique momentanément indisponible" if scenario == "EBOOKS" else "Catalogue physique momentanément indisponible"
                    )
                    continue
            except httpx.HTTPError as exc:
                # Transport/auth/status failures do not need a same-endpoint
                # re-POST with smaller result size. Keep the branch independent.
                failures.append((scenario, exc))
                caption = "Catalogue numérique" if scenario == "EBOOKS" else "Catalogue physique"
                status = (
                    exc.response.status_code
                    if isinstance(exc, httpx.HTTPStatusError) else None
                )
                if status == 401:
                    warning = caption + " : authentification requise"
                elif status == 403:
                    warning = caption + " : accès refusé"
                else:
                    warning = caption + " momentanément indisponible"
                self.last_search_warnings.append(warning)
                continue

            # Facets are a descriptor of this successfully returned branch
            # and *page*, not any particular book nor the whole catalogue.
            capture = self._last_scenario_facet_observation
            if (capture is not None and capture[0] == scenario
                    and capture[1] == page):
                self.last_search_response_facets.extend(capture[2])
            # An empty result from an available catalogue is still a
            # successful search, not evidence that every route failed.
            successful_scenarios += 1
            for item in rows or []:
                if item.id not in seen:
                    seen.add(item.id)
                    out.append(item)

        if failures and successful_scenarios:
            self.last_search_warnings = [
                "partial_results : " + warning
                for warning in self.last_search_warnings
            ]
        if failures and not successful_scenarios:
            # No public stale fallback on a verified 401/403, even when
            # another branch had a timeout. Inspect httpx's numeric response
            # only, never exception text, request URL or private payload.
            http_statuses = [
                error.response.status_code for _name, error in failures
                if isinstance(error, httpx.HTTPStatusError)
            ]
            if 403 in http_statuses:
                error = ParisSearchUnavailable("Accès au catalogue Paris refusé.")
                error.code = "search_access_denied"
                error.retryable = False
            elif 401 in http_statuses:
                error = ParisSearchUnavailable("Authentification requise pour le catalogue Paris.")
                error.code = "search_authentication_required"
                error.retryable = False
            else:
                message = (
                    "Le catalogue numérique n'a pas répondu à cette recherche. Essayez un terme plus précis ou réessayez dans quelques instants."
                    if scope == "digital" else
                    "Le catalogue physique n'a pas répondu à cette recherche. Réessayez dans quelques instants."
                    if scope == "physical" else
                    "Les catalogues Paris n'ont pas répondu à cette recherche. Réessayez dans quelques instants."
                )
                error = ParisSearchUnavailable(message)
            # Do not chain raw Syracuse errors (which can contain the
            # request URL, personal cookies, or provider response text).
            raise error
        return out

    def _holdings_payload(self, docbase: str, record_id: str, scenario: str, search_terms: str = "") -> dict[str, Any]:
        """Return the raw GetListHoldings payload.

        The normalized holdings view intentionally discards HtmlView, but the
        official Paris PNB borrow flow publishes its signed entry point there.
        """
        query = {
            "FacetFilter": "{}", "ForceSearch": False, "InitialSearch": False, "Page": 0, "PageRange": 3,
            "QueryGuid": str(uuid.uuid4()), "QueryString": search_terms, "ResultSize": 50, "ScenarioCode": scenario,
            "ScenarioDisplayMode": "display-standard", "SearchGridFieldsShownOnResultsDTO": [], "SearchLabel": "",
            "SearchTerms": search_terms, "SortField": None, "SortOrder": 0,
            "TemplateParams": {"Scenario": "", "Scope": "Default", "Size": None, "Source": "", "Support": "", "UseCompact": False},
            "Url": f"{self.base_url}/search.aspx?SC={scenario}", "UseSpellChecking": None,
        }
        r = self.client.post(
            f"{self.base_url}{HOLDINGS_PATH}",
            headers={**self._api_headers(), "Content-Type": "application/json; charset=UTF-8", "Origin": self.base_url, "Referer": f"{self.base_url}/search.aspx?SC={scenario}"},
            json={"Record": {"RscId": record_id, "Docbase": docbase}, "searchQuery": query},
        )
        return self._json(r, "holdings")

    def holdings(self, docbase: str, record_id: str, scenario: str, search_terms: str = "") -> ItemDetail:
        def work():
            data = self._holdings_payload(docbase, record_id, scenario, search_terms)
            return normalize_item_detail(docbase, record_id, data)
        return self._timed("CATALOG", "holdings", work)

    def _set_pnb_borrow_phase(self, phase: str, **facts: Any) -> None:
        safe: dict[str, Any] = {"phase": str(phase)}
        for key, value in facts.items():
            if key in {"record_id", "url", "href", "location", "token", "cookie", "authorization"}:
                continue
            if isinstance(value, (str, int, float, bool)) or value is None:
                safe[str(key)] = value
        self.last_pnb_borrow = safe
        try:
            self.diagnostics.add("PNB", "borrow phase", "ok" if phase not in {"failed"} else "error", 0, safe)
        except Exception:
            pass

    @staticmethod
    def _pnb_loan_href(payload: dict[str, Any]) -> str:
        """Extract the official pnb-loan ASSARedirect href from HtmlView."""
        d = payload.get("d") if isinstance(payload.get("d"), dict) else {}
        item_holding = d.get("ItemHoldingsData") if isinstance(d.get("ItemHoldingsData"), dict) else {}
        if item_holding.get("CanLoan") is not True:
            _TRACE_LOG.warning("TRACE paris pnb-loan blocked phase=availability can_loan=false_or_missing")
            label = str(item_holding.get("HoldingLabel") or "indisponible")
            raise ParisError(f"Emprunt PNB impossible : {label}")
        html_view = d.get("HtmlView")
        if not isinstance(html_view,str):html_view=""
        # Parse HTML attributes, not a regex requiring a specific quote style.
        # Decode only for classification: use the server's original URL for navigation.
        class Links(HTMLParser):
            def __init__(self):
                super().__init__(convert_charrefs=True)
                self.hrefs = []
                self.forms = 0
                self.post_forms = 0
                self.form_actions = 0

            def handle_starttag(self, tag, attrs):
                attrs = {str(k).casefold(): v for k, v in attrs if k is not None}
                if tag.casefold() == "a":
                    href = attrs.get("href")
                    if type(href) is str and href.strip():
                        self.hrefs.append(href.strip())
                elif tag.casefold() == "form":
                    self.forms += 1
                    if str(attrs.get("method") or "").casefold() == "post":
                        self.post_forms += 1
                    if type(attrs.get("action")) is str and attrs["action"].strip():
                        self.form_actions += 1

        # Keep all actionable candidates under the pre-existing exact
        # selector. The counters below are closed diagnostic classes only:
        # observing a form, a DOM marker, or an unknown host does NOT grant
        # permission to navigate, send a POST or infer a signed borrow URL.
        links = Links()
        links.feed(html_view[:2_000_000])
        candidates = []
        assaredirect_hrefs = 0
        loan_book_hrefs = 0
        rejected_scheme = 0
        malformed_hrefs = 0
        external_hrefs = 0
        rejected_candidate_origin = 0
        expected_host = urlparse(BASE_URL).hostname
        for href in links.hrefs:
            decoded = href
            for _ in range(2):
                decoded = unquote(decoded)
            try:
                parsed = urlparse(decoded)
                href_site = public_site(urljoin(BASE_URL, decoded))
            except ValueError:
                malformed_hrefs += 1
                continue
            if parsed.scheme and parsed.scheme.casefold() not in {"http", "https"}:
                rejected_scheme += 1
            if href_site == "external":
                external_hrefs += 1
            path_known = parsed.path.casefold().endswith("/assaredirect.ashx")
            loan_marker = "loan-book.ashx" in decoded.casefold()
            assaredirect_hrefs += int(path_known)
            loan_book_hrefs += int(loan_marker)
            if path_known and loan_marker:
                # A lookalike link is not a valid Paris borrow candidate.
                # Validate both the decoded shape used for discovery and the
                # ORIGINAL href passed to urljoin by the redirect resolver.
                # Never query either URL merely to verify its provenance.
                try:
                    original_target = urlparse(urljoin(BASE_URL + "/", href))
                    decoded_target = urlparse(urljoin(BASE_URL + "/", decoded))
                    valid_origin = all(
                        target.scheme == "https"
                        and target.hostname == expected_host
                        and target.username is None and target.password is None
                        and target.port in (None, 443)
                        for target in (original_target, decoded_target)
                    )
                except ValueError:
                    valid_origin = False
                if not valid_origin:
                    rejected_candidate_origin += 1
                    continue
                if href not in candidates:
                    candidates.append(href)
        selected_site = (
            public_site(urljoin(BASE_URL, candidates[0]))
            if len(candidates) == 1 else "unknown"
        )
        _TRACE_LOG.info(
            "TRACE paris pnb-loan link-discovery can_loan=true "
            "html_present=%s html_chars=%s anchors=%s candidates=%s "
            "forms=%s post_forms=%s form_actions=%s "
            "assaredirect_hrefs=%s loan_book_hrefs=%s "
            "dom_assaredirect=%s dom_loan_book=%s "
            "rejected_scheme=%s malformed_hrefs=%s external_hrefs=%s "
            "rejected_candidate_origin=%s candidate_site=%s",
            bool(html_view), len(html_view), len(links.hrefs), len(candidates),
            links.forms, links.post_forms, links.form_actions,
            assaredirect_hrefs, loan_book_hrefs,
            "assaredirect.ashx" in html_view.casefold(),
            "loan-book.ashx" in html_view.casefold(),
            rejected_scheme, malformed_hrefs, external_hrefs,
            rejected_candidate_origin, selected_site,
        )
        if len(candidates)==1:return candidates[0]
        reason='ambiguous_link' if candidates else 'missing_link'
        _TRACE_LOG.warning("TRACE paris pnb-loan blocked phase=link-discovery reason=%s",reason)
        raise ParisError("GetListHoldings n'a pas fourni un lien officiel d'emprunt PNB unique "
                         "(lien absent ou ambigu). Aucun emprunt lancé ; actualisez la disponibilité ou vérifiez le parcours sur le site Paris.")

    def _validate_pnb_redirect_url(self, url: str) -> None:
        try:
            parsed = urlparse(url)
            host = (parsed.hostname or "").casefold()
            port = parsed.port
        except ValueError:
            # Never echo the input URL: it can carry signed delivery tokens.
            raise ParisRedirectError("") from None
        base_host = (urlparse(self.base_url).hostname or "").casefold()
        existing_host = (
            host == base_host
            or host == "marketplace.syracuse.cloud" or host.endswith(".syracuse.cloud")
            or host == "pnb-dilicom.centprod.com"
            or host == "numilog.com" or host.endswith(".numilog.com")
        )
        # Exact delivery hosts observed in authenticated Paris diagnostics.
        # edenlivres and edelivres are distinct observed hosts, never aliases.
        # A signed loan/ACSM redirect must never downgrade to plaintext HTTP
        # or navigate to an arbitrary service port, INCLUDING for legacy
        # "existing_host" entries. This check is run before every GET hop.
        supplier_host = host in {
            "www.edenlivres.fr", "www.cantook.net", "acs4.edelivres.fr",
            "acs4.edenlivres.fr", "acs4.cantook.net",
        }
        if (parsed.scheme != "https" or port not in (None, 443)
                or parsed.username is not None or parsed.password is not None
                or not (existing_host or supplier_host)):
            _TRACE_LOG.warning(
                "PNB redirect refused site=%s reason=host_scheme_port_or_credentials",
                public_site(url),
            )
            raise ParisRedirectError(host)

    @staticmethod
    def _pnb_trace_path(url: str) -> str:
        """Return a log-safe path; DILICOM LOAN URLs carry opaque tokens."""
        parsed = urlparse(url)
        host = (parsed.hostname or "").casefold()
        if host == "pnb-dilicom.centprod.com" and "/LOAN/" in parsed.path.upper():
            prefix = re.split(r"/LOAN/", parsed.path, maxsplit=1, flags=re.I)[0]
            return f"{prefix}/LOAN/<redacted>"
        if host in {"www.edenlivres.fr", "www.cantook.net", "acs4.edelivres.fr", "acs4.edenlivres.fr", "acs4.cantook.net"}:
            return "/<redacted>"
        # Delivery paths can contain opaque credentials on any supplier.
        if parsed.path.endswith("ASSARedirect.ashx"):
            return "/ASSARedirect.ashx"
        return "/<redacted>"

    @staticmethod
    def _pnb_trace_response(response, *, hop, elapsed_ms):
        location = response.headers.get("location") or ""
        try:
            target = urlparse(urljoin(str(response.url), location)) if location else None
            site = public_site(target.geturl()) if target else "unknown"
            scheme = target.scheme if target and target.scheme in {"https", "http"} else ("other" if target else None)
            port = target.port if target else None
            acsm = bool(target and target.path.casefold().endswith(".acsm"))
        except ValueError:
            site, scheme, port, acsm = "unknown", "invalid", None, False
        _TRACE_LOG.info(
            "TRACE paris pnb-loan hop-response hop=%s status=%s target_site=%s target_scheme=%s target_port=%s target_acsm=%s dt_ms=%s",
            hop, public_http_status(response.status_code), site, scheme, port, acsm, elapsed_ms,
        )

    def _get_pnb_response(self, url, **kwargs):
        # httpx builds next_request even with follow_redirects=False. A malformed
        # Location can therefore fail before our per-hop URL validation.
        try:
            return self.client.get(url, follow_redirects=False, **kwargs)
        except httpx.RemoteProtocolError as exc:
            if "Invalid URL in location header:" in str(exc):
                raise ParisRedirectError("") from None
            raise

    def _resolve_pnb_loan_to_acsm(self, href: str, *, max_redirects: int = 12) -> str:
        """Follow the official signed PNB redirect chain and stop before ACSM GET."""
        current = urljoin(self.base_url + "/", href)
        base_host = (urlparse(self.base_url).hostname or "").casefold()
        parsed_start = urlparse(current)
        if (parsed_start.hostname or "").casefold() != base_host or "ASSARedirect.ashx" not in parsed_start.path:
            raise ParisError("Lien PNB initial invalide: ASSARedirect attendu sur Bibliothèques de Paris")

        navigation_headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.7",
            "Referer": f"{self.base_url}/numerique/",
        }
        for hop in range(max_redirects):
            self._validate_pnb_redirect_url(current)
            parsed = urlparse(current)
            _TRACE_LOG.info(
                "TRACE paris pnb-loan redirect-request hop=%s site=%s",
                hop + 1, public_site(current),
            )
            hop_started = time.monotonic()
            response = self._get_pnb_response(current, headers=navigation_headers)
            self._pnb_trace_response(response, hop=hop + 1, elapsed_ms=int((time.monotonic()-hop_started)*1000))
            location = response.headers.get("location") or ""
            _TRACE_LOG.info(
                "TRACE paris pnb-loan redirect-response hop=%s status=%s site=%s has_location=%s",
                hop + 1, public_http_status(response.status_code), public_site(current), bool(location),
            )
            if response.status_code not in {301, 302, 303, 307, 308}:
                raise ParisError(
                    f"Parcours PNB interrompu avant l'ACSM (HTTP {response.status_code} sur {parsed.hostname or 'hôte inconnu'})"
                )
            if not location:
                raise ParisError("Parcours PNB interrompu: redirection sans en-tête Location")
            next_url = urljoin(str(response.url), location)
            self._validate_pnb_redirect_url(next_url)
            next_parsed = urlparse(next_url)
            if next_parsed.path.casefold().endswith(".acsm") or "urllink.acsm" in next_parsed.path.casefold():
                _TRACE_LOG.info(
                    "TRACE paris pnb-loan acsm-ready hop=%s site=%s",
                    hop + 1, public_site(next_url),
                )
                return next_url
            current_host = (urlparse(str(response.url)).hostname or "").casefold()
            next_host = (next_parsed.hostname or "").casefold()
            if current_host == next_host:
                navigation_headers["Referer"] = str(response.url)
            else:
                response_parsed = urlparse(str(response.url))
                navigation_headers["Referer"] = f"{response_parsed.scheme}://{response_parsed.netloc}/"
            current = next_url
        raise ParisError(f"Parcours PNB trop long ({max_redirects} redirections) sans URL ACSM")

    def borrow_digital(self, item: SearchResult, uid: str = "") -> Loan:
        """Create a real DILICOM loan through the captured official PNB flow."""
        if item.docbase.upper() != "DILICOM" or item.medium != "digital" or not item.record_id:
            raise ParisError("Seuls les livres numériques DILICOM peuvent être empruntés via ce parcours")
        if uid:
            raise ParisError("L'emprunt direct PNB d'un compte délégué n'est pas encore validé")

        record_ref = hashlib.sha256(str(item.record_id).encode()).hexdigest()[:12]
        self._set_pnb_borrow_phase("start", record_ref=record_ref, delegated=False)

        def work() -> Loan:
            self._set_pnb_borrow_phase("existing-loan", record_ref=record_ref)
            _TRACE_LOG.info("TRACE paris pnb-loan begin docbase=DILICOM phase=existing-loan")
            existing = next((row for row in self.loans("DILICOM", uid)
                if str(row.record_id or "") == str(item.record_id)), None)
            if existing is not None:
                self._set_pnb_borrow_phase("completed", record_ref=record_ref, mutation=False, existing=True)
                _TRACE_LOG.info("TRACE paris pnb-loan existing-confirmed mutation=skipped")
                return existing
            self._set_pnb_borrow_phase("holdings", record_ref=record_ref)
            payload = self._holdings_payload(item.docbase, item.record_id, item.scenario or "EBOOKS", item.title)
            _TRACE_LOG.info("TRACE paris pnb-loan phase=holdings-read payload_dict=%s d_dict=%s",isinstance(payload,dict),isinstance(payload.get('d'),dict))
            self._set_pnb_borrow_phase("link-discovery", record_ref=record_ref)
            href = self._pnb_loan_href(payload)
            self._set_pnb_borrow_phase("redirect", record_ref=record_ref, mutation=True)
            _TRACE_LOG.info("TRACE paris pnb-loan phase=redirect-dispatch mutation_requested=true")
            acsm_url = self._resolve_pnb_loan_to_acsm(href)
            self._set_pnb_borrow_phase("provider-confirmation", record_ref=record_ref, mutation=True)
            for attempt in range(4):
                rows = self.loans("DILICOM", uid)
                loan = next((row for row in rows if str(row.record_id or "") == str(item.record_id)), None)
                _TRACE_LOG.info("TRACE paris pnb-loan phase=provider-confirmation attempt=%s confirmed=%s results=%s",attempt+1,loan is not None,len(rows))
                if loan is not None:
                    loan.ebook_url = acsm_url
                    self._set_pnb_borrow_phase("completed", record_ref=record_ref, mutation=True, existing=False, confirmation_attempt=attempt+1)
                    return loan
                if attempt < 3:
                    time.sleep(0.35 * (attempt + 1))
            raise ParisError(
                "Le serveur PNB a exécuté le parcours d'emprunt mais ListLoans(DILICOM) n'a pas confirmé le prêt"
            )

        trace=uuid.uuid4().hex[:12];token=_BORROW_TRACE.set(trace)
        _TRACE_LOG.info("TRACE paris pnb-loan operation=borrow record_ref=%s delegated=false",record_ref)
        try:
            return self._timed("PNB", "borrow digital", work)
        except Exception as exc:
            previous = str((getattr(self, "last_pnb_borrow", {}) or {}).get("phase") or "unknown")
            self._set_pnb_borrow_phase("failed", record_ref=record_ref, failed_from=previous, error=type(exc).__name__)
            raise
        finally:
            _BORROW_TRACE.reset(token)

    def pnb_preferences(self) -> dict[str, Any]:
        def work():
            r = self.client.get(f"{self.base_url}{ACCOUNT_SERVICE}/GetUserPnbPreferences", headers=self._api_headers())
            data = self._json(r, "PNB preferences", require_success=False)
            return data
        return self._timed("PNB", "preferences", work)

    def download_digital_document(self, loan: Loan, uid: str = "", preferences: dict[str, Any] | None = None) -> str:
        if loan.provider != "DILICOM" or not loan.record_id:
            raise ParisError("Ce prêt n'est pas un document numérique DILICOM téléchargeable")
        preferences = preferences or self.pnb_preferences()
        passphrase = preferences.get("PassPhrase") or ""
        hint = preferences.get("Hint") or ""
        reader_id = preferences.get("ReaderId") or ""
        if not reader_id:
            raise ParisError("Aucun ReaderId PNB n'est configuré sur le compte")
        def work():
            endpoint = f"{self.base_url}{ACCOUNT_SERVICE}/DownloadDigitalDocument"
            _TRACE_LOG.info(
                "TRACE download paris endpoint request book_id=%s site=%s uid_present=%s reader_id_present=%s endpoint=DownloadDigitalDocument",
                public_book_id(loan.record_id), public_site(self.base_url),
                bool(uid), bool(reader_id),
            )
            r = self.client.post(
                endpoint,
                headers={**self._api_headers(), "Content-Type": "application/json; charset=UTF-8"},
                json={
                    "serviceCode": loan.provider,
                    "ean": loan.record_id,
                    "userUniqueIdentifier": uid,
                    "actualPassPhrase": passphrase,
                    "actualHint": hint,
                    "actualReaderId": reader_id,
                },
            )
            _TRACE_LOG.info(
                "TRACE download paris endpoint response book_id=%s site=%s status=%s content_type=%s bytes=%s",
                public_book_id(loan.record_id), public_site(self.base_url),
                public_http_status(r.status_code),
                public_content_type(r.headers.get("content-type", "")), len(r.content or b""),
            )
            data = self._json(r, "download digital document")
            value = data.get("d")
            if not isinstance(value, str) or not value:
                raise ParisError("Paris n'a pas renvoyé d'URL de téléchargement")
            return value
        return self._timed("PNB", "download digital document", work)


    def cancel_booking(self, booking: Booking, uid: str = "") -> dict[str, Any]:
        if not booking.raw:
            raise ParisError("L'objet réservation Syracuse brut est requis pour l'annulation")
        def work():
            r = self.client.post(
                f"{self.base_url}{ACCOUNT_SERVICE}/CancelBookings",
                headers={**self._api_headers(), "Content-Type": "application/json; charset=UTF-8"},
                json={"bookings": [booking.raw], "userUniqueIdentifier": uid},
            )
            return self._json(r, "cancel booking")
        return self._timed("ACTION", "cancel booking", work)

    def save_pnb_preferences(self, passphrase: str, hint: str, reader_id: str, reader_app_name: str, uid: str = "") -> dict[str, Any]:
        """Interface Syracuse découverte dans le JS officiel.

        Attention: Syracuse place ces valeurs sensibles dans la query string.
        Cette méthode ne journalise jamais les paramètres.
        """
        def work():
            r = self.client.get(
                f"{self.base_url}{ACCOUNT_SERVICE}/SavePnbPreferences",
                headers=self._api_headers(),
                params={
                    "passPhrase": passphrase,
                    "hint": hint,
                    "readerId": reader_id,
                    "readerAppName": reader_app_name,
                    "userUniqueIdentifier": uid,
                },
            )
            return self._json(r, "save PNB preferences", require_success=False)
        return self._timed("PNB", "save preferences", work)

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
        if not loan.renewable:
            raise ParisError(loan.cannot_renew_reason or "Ce prêt n'est pas renouvelable")
        def work():
            r = self.client.post(
                f"{self.base_url}{ACCOUNT_SERVICE}/RenewLoans",
                headers={**self._api_headers(), "Content-Type": "application/json; charset=UTF-8"},
                json={"loans": [loan.raw], "userUniqueIdentifier": uid},
            )
            return self._json(r, "renew loan")
        return self._timed("ACTION", "renew loan", work)

    def return_digital_loan(self, loan: Loan, uid: str = "") -> dict[str, Any]:
        if loan.provider != "DILICOM" or not loan.can_return:
            raise ParisError("Ce prêt numérique n'est pas marqué comme restituable")
        def work():
            r = self.client.post(
                f"{self.base_url}{ACCOUNT_SERVICE}/ReturnDigitalLoan",
                headers={**self._api_headers(), "Content-Type": "application/json; charset=UTF-8"},
                json={"serviceCode": "DILICOM", "id": loan.id, "userUniqueIdentifier": uid},
            )
            return self._json(r, "return digital loan")
        token=_BORROW_TRACE.set(uuid.uuid4().hex[:12])
        _TRACE_LOG.info("TRACE paris provider-return phase=dispatch loan_ref=%s delegated=%s",hashlib.sha256(str(loan.id).encode()).hexdigest()[:12],bool(uid))
        try:
            result=self._timed("ACTION", "return digital loan", work)
            _TRACE_LOG.info("TRACE paris provider-return phase=response-accepted server_request_accepted=true provider_inventory_verified=false")
            return result
        except Exception as exc:
            _TRACE_LOG.warning("TRACE paris provider-return phase=failed error=%s",type(exc).__name__);raise
        finally:_BORROW_TRACE.reset(token)
