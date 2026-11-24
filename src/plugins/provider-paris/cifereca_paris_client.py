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


from cifereca_paris_transport import ParisTransportOperations


class ParisClient(ParisCatalogueOperations, ParisPnbOperations, ParisTransportOperations):
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
