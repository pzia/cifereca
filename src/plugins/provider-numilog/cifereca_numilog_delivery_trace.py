"""Privacy-preserving per-action Numilog delivery observability (#1884).

No URLs, HTTP bodies, book IDs, account IDs, cookies, signatures, credentials,
exception messages or filesystem paths are ever carried by this object.
"""
from __future__ import annotations

from secrets import token_hex
from time import perf_counter
from urllib.parse import parse_qsl, urlsplit
from unicodedata import category

_PUBLIC_PHASES = frozenset({
    "start", "delivery_http", "html_selection", "artifact_validation",
    "adept_fulfillment", "completed",
})
_PUBLIC_SOURCES = frozenset({"readbook", "owned_card", "direct_url"})
_PUBLIC_HTML = frozenset({"login", "error", "intermediate", "invalid", "unknown"})
_PUBLIC_MIME = frozenset({"html", "acsm_xml", "xml", "epub", "binary", "other"})
_PUBLIC_CODES = frozenset({
    "started", "redirect", "http_ok", "single_allowed_candidate",
    "html_delivery_link_missing", "html_delivery_ambiguous",
    "session_expired_confirmed", "login_form_detected", "html_error", "html_invalid",
    "http_forbidden", "http_unauthorized", "http_rate_limited",
    "link_expired", "http_error", "origin_rejected", "redirect_loop",
    "redirect_limit", "mime_unexpected", "download_oversize",
    "artifact_invalid", "xml_not_acsm", "adept_attention_required",
    "adept_unconfirmed", "epub_complete", "epub_candidate", "acsm_complete", "acsm_candidate",
    "html_oversize",
    "unclassified_error",
})


def public_http_code(value: object) -> str:
    if type(value) is not int:
        return "http_error"
    if value == 401:
        return "http_unauthorized"
    if value == 403:
        return "http_forbidden"
    if value == 410:
        return "link_expired"
    if value == 429:
        return "http_rate_limited"
    return "http_error"


def destination_category(value: object) -> tuple[str, str]:
    """Return fixed origin/path classes, never a real URL, host or query."""
    if type(value) is not str:
        return "other", "other"
    try:
        parts = urlsplit(value)
        host = (parts.hostname or "").casefold()
        path = parts.path.casefold()
    except ValueError:
        return "other", "other"
    origin = {
        "entrepot2r.numilog.com": "acsm_fulfillment",
        "www.numilog.com": "numilog_site",
        "services.numilog.com": "numilog_service",
        "www.edenlivres.fr": "eden",
        "storage.googleapis.com": "cdn",
    }.get(host, "other")
    path_kind = (
        "acsm_fulfillment" if path == "/fulfillment/urllink.acsm"
        else "readbook" if path.endswith("/readbook.ashx")
        else "personal_library" if path.endswith("/meslivres.aspx")
        else "login_page" if host == "www.numilog.com" and path in {"/login.aspx", "/login.aspx/authuser"}
        else "confirmation_page" if _is_confirmation_url(value)
        else "other"
    )
    return origin, path_kind


def _is_confirmation_url(value: object) -> bool:
    """Recognize only the canonical Numilog HTTPS confirmation path.

    No order reference, URL or query is ever returned from this predicate.
    """
    if type(value) is not str or any(
        category(char) in {"Cc", "Cf", "Cs"} or ord(char) == 92
        for char in value
    ):
        return False
    try:
        parts = urlsplit(value)
        return (
            parts.scheme.casefold() == "https"
            and (parts.hostname or "").casefold() == "www.numilog.com"
            and parts.port in (None, 443)
            and parts.username is None and parts.password is None
            and not parts.fragment
            and parts.path.casefold() == "/pages/commande/confirmationpaiement.aspx"
        )
    except (ValueError, AttributeError):
        return False


def confirmation_query_shape(value: object) -> dict[str, bool]:
    """Return *only* fixed booleans for an exact confirmation-page URL.

    A private ref/disp value is never copied, logged or serialized.
    Duplicated/empty values do not attest a single unambiguous parameter.
    """
    if not _is_confirmation_url(value):
        return {}
    try:
        parts = urlsplit(value)
        pairs = parse_qsl(
            parts.query, keep_blank_values=True,
            strict_parsing=True, max_num_fields=32,
        )
    except (ValueError, AttributeError):
        return {
            "confirmation_ref_present": False,
            "confirmation_disp_simple": False,
        }
    refs = [v for k, v in pairs if k == "ref"]
    displays = [v for k, v in pairs if k == "disp"]
    return {
        "confirmation_ref_present": len(refs) == 1 and bool(refs[0].strip()),
        "confirmation_disp_simple": displays == ["simple"],
    }


def mime_group(value: object) -> str:
    """Publish a fixed MIME *category*; never the upstream header."""
    if type(value) is not str:
        return "other"
    mime = value.split(";", 1)[0].strip().lower()
    return {
        "text/html": "html",
        "application/vnd.adobe.adept+xml": "acsm_xml",
        "application/xml": "xml",
        "text/xml": "xml",
        "application/epub+zip": "epub",
        "application/octet-stream": "binary",
        "application/zip": "binary",
    }.get(mime, "other")


class DeliveryTrace:
    """One transient correlation token per explicit acquisition attempt.

    Only the terminal event is normal-level; per-hop events are detailed,
    preserving normal-operation overhead and confidentiality.
    """

    def __init__(self, diagnostics, *, source: str = "direct_url", connected_initial: object = None):
        self._diagnostics = diagnostics
        self._connection = ({"connected_initial": connected_initial}
                            if type(connected_initial) is bool else {})
        self._source = source if type(source) is str and source in _PUBLIC_SOURCES else "direct_url"
        self._token = token_hex(6)  # random, non-reversible; never an account/item ID
        self._started = perf_counter()
        self._error_code = "unclassified_error"
        self._success_code = ""
        self._hops = 0

    def fail(self, code: str) -> None:
        self._error_code = code if type(code) is str and code in _PUBLIC_CODES else "unclassified_error"

    def event(
        self, phase: str, code: str, *, status: object = None,
        mime: object = None, html_kind: object = None, hops: object = None,
        destination: object = None, candidates: object = None,
        cookie_header_present: object = None, redirect_target: object = None,
    ) -> None:
        if phase not in _PUBLIC_PHASES or code not in _PUBLIC_CODES:
            return
        detail: dict[str, object] = {
            "action": self._token, "source": self._source,
            "phase": phase, "code": code,
            **self._connection,
        }
        if type(status) is int and 100 <= status <= 599:
            detail["http_status"] = status
        if mime is not None:
            detail["mime"] = mime_group(mime)
        if destination is not None:
            detail["origin_kind"], detail["path_kind"] = destination_category(destination)
            if detail["path_kind"] == "confirmation_page":
                detail.update(confirmation_query_shape(destination))
        # Only a previously checked/allowlisted URL should be supplied by
        # callers. Even then, export fixed enums only, never Location/query.
        # A rejected/unverified redirect must omit both fields entirely.
        if type(redirect_target) is str:
            detail["redirect_target_origin_kind"], detail["redirect_target_kind"] = (
                destination_category(redirect_target)
            )
            if detail["redirect_target_kind"] == "confirmation_page":
                for key, flag in confirmation_query_shape(redirect_target).items():
                    detail["redirect_" + key] = flag
        if html_kind is not None:
            detail["html_kind"] = (
                html_kind if type(html_kind) is str and html_kind in _PUBLIC_HTML
                else "unknown"
            )
        # A boolean presence signal only: never serialize Cookie contents, names,
        # domains or count. Presence is NOT proof of a valid login session.
        if type(cookie_header_present) is bool:
            detail["cookie_header_present"] = cookie_header_present
        if type(candidates) is int and 0 <= candidates <= 10_000:
            detail["candidates"] = candidates
        if type(hops) is int and 0 <= hops <= 12:
            detail["hops"] = hops
            self._hops = hops
        self._emit(detail, level="detailed", status="ok")

    def success(self, code: str) -> None:
        self._success_code = code if code in {"epub_complete", "epub_candidate", "acsm_complete", "acsm_candidate"} else ""

    def _emit(self, detail: dict[str, object], *, level: str, status: str) -> None:
        if self._diagnostics is None:
            return
        try:
            self._diagnostics.add(
                "NUMILOG", "acquisition_delivery", status,
                detail=detail, level=level,
                duration_ms=(
                    min(600_000, max(0, int((perf_counter() - self._started) * 1000)))
                    if level == "normal" else None
                ),
            )
        except Exception:
            # Diagnostics must never break or change an acquisition outcome.
            pass

    def __enter__(self):
        self.event("start", "started", hops=0)
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        successful = exc_type is None and bool(self._success_code)
        self._emit(
            {
                "action": self._token, "source": self._source,
                "phase": "completed",
                "code": self._success_code if successful else self._error_code,
                "hops": self._hops,
                **self._connection,
            },
            level="normal",
            status="ok" if successful else "error",
        )
        return False


__all__ = [
    "DeliveryTrace", "confirmation_query_shape", "destination_category",
    "mime_group", "public_http_code",
]
