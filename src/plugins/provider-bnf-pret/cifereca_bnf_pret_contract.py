"""Offline-only contract for the *unqualified* BnF Prêt numérique API v1.

This file is deliberately NOT registered as a provider. The documented Basic
API key has not been established as available to an individual Pass holder.
No HTTP, authentication, ACSM handling, or mutation is implemented here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping
from urllib.parse import urlsplit

API_ORIGIN = "https://pret.bnf.fr"
PORTAL_URL = "https://pret.bnf.fr/"
MAX_PAGE = 1000
SUPPORTED_SEARCH_FIELDS = frozenset({"q", "title", "author", "publisher", "isbn", "start_at"})
# No capability may be advertised to Core until the reader-level permission
# and its actual authentication protocol have been independently qualified.
READER_CAPABILITIES = frozenset()

KNOWN_ERRORS = frozenset({
    "access_refused", "resource_not_found", "missing_member_id", "invalid_email",
    "email_already_exists", "loans_limit_reached", "member_downloaded_limit_reached",
    "member_downloaded_daily_limit_reached", "member_downloaded_monthly_limit_reached",
    "no_loan_available", "medium_not_found", "loan_not_found", "loan_expired",
    "loan_hold_expired",
})


class BnFPretContractError(ValueError):
    """Stable, safe-to-log error code (no upstream message or URL)."""

    def __init__(self, code: str, *, status: int | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.status = status


def error_code(status: int, payload: Any = None) -> str:
    """Prefer documented business codes; never reflect arbitrary server data."""
    if isinstance(payload, Mapping):
        codes = payload.get("errors")
        if isinstance(codes, list):
            for code in codes:
                if isinstance(code, str) and code in KNOWN_ERRORS:
                    return code
    if status == 401:
        return "auth_required"
    if status == 403:
        return "access_forbidden"
    if status == 404:
        return "not_found"
    if status == 429:
        return "rate_limited"
    if status >= 500:
        return "upstream_unavailable"
    return "unexpected_response"


def _number(value: Any, name: str, *, minimum: int = 0, maximum: int = 2_000_000_000) -> int:
    if isinstance(value, bool) or not (isinstance(value, int) or isinstance(value, str) and value.isascii() and value.isdecimal()):
        raise BnFPretContractError(f"invalid_{name}")
    number = int(value)
    if not minimum <= number <= maximum:
        raise BnFPretContractError(f"invalid_{name}")
    return number


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 512:
        raise BnFPretContractError(f"invalid_{name}")
    return value.strip()


def _date(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > 40:
        raise BnFPretContractError("invalid_date")
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BnFPretContractError("invalid_date") from exc
    return value


def _public_cover(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        url = urlsplit(value)
        if (url.scheme == "https" and url.hostname == "assets.cantook.net"
                and not url.username and not url.password and not url.port
                and not url.query and not url.fragment and url.path.startswith("/assets/")):
            return value
    except ValueError:
        pass
    return None


@dataclass(frozen=True)
class MediumRecord:
    id: str
    kind: str
    isbn: str | None
    available_copies: int | None
    total_copies: int | None
    current_holds: int | None


@dataclass(frozen=True)
class ResourceRecord:
    id: str
    title: str
    author: str | None
    publisher: str | None
    cover_url: str | None
    media: tuple[MediumRecord, ...]
    # This API describes copies/licences, NOT this reader's entitlement.
    owned: bool = False


@dataclass(frozen=True)
class LoanRecord:
    id: str
    member_id: str
    resource_id: str
    expires_at: str | None
    hold_expires_at: str | None


@dataclass(frozen=True)
class Page:
    items: tuple[ResourceRecord, ...] | tuple[LoanRecord, ...]
    current: int
    total_pages: int
    total_items: int
    per_page: int

    @property
    def next_page(self) -> int | None:
        return self.current + 1 if self.current < self.total_pages else None


def search_parameters(*, page: int = 1, **fields: str) -> dict[str, str]:
    """Only documented filters; constructs data, never sends a request."""
    page = _number(page, "page", minimum=1, maximum=MAX_PAGE)
    if fields.keys() - SUPPORTED_SEARCH_FIELDS:
        raise BnFPretContractError("unsupported_search_filter")
    params = {"page": str(page)}
    for key, value in fields.items():
        if value is not None:
            params[key] = _text(value, key)
    if not fields or not any(key != "start_at" for key in fields):
        raise BnFPretContractError("missing_search_filter")
    return params


def _resource(data: Any) -> ResourceRecord:
    if not isinstance(data, Mapping):
        raise BnFPretContractError("invalid_resource")
    raw_media = data.get("media", [])
    if not isinstance(raw_media, list):
        raise BnFPretContractError("invalid_media")
    media = []
    for raw in raw_media:
        if not isinstance(raw, Mapping):
            raise BnFPretContractError("invalid_media")
        raw_loans = raw.get("loans")
        if raw_loans is not None and not isinstance(raw_loans, Mapping):
            raise BnFPretContractError("invalid_loans")
        available = (raw_loans or {}).get("available")
        total = (raw_loans or {}).get("total")
        holds = raw.get("current_holds")
        available = _number(available, "availability") if available is not None else None
        total = _number(total, "availability") if total is not None else None
        holds = _number(holds, "holds") if holds is not None else None
        if available is not None and total is not None and available > total:
            raise BnFPretContractError("invalid_availability")
        isbn = raw.get("key") if str(raw.get("key_type", "")).lower() in {"isbn", "isbn13"} else None
        media.append(MediumRecord(
            id=_text(raw.get("id"), "medium_id"),
            kind=_text(raw.get("nature"), "medium_kind"),
            isbn=_text(isbn, "isbn") if isbn is not None else None,
            available_copies=available, total_copies=total, current_holds=holds,
        ))
    contributors = data.get("contributors")
    author = None
    if isinstance(contributors, list):
        for person in contributors:
            if isinstance(person, Mapping) and person.get("nature") == "author":
                author = " ".join(str(person.get(x) or "").strip() for x in ("first_name", "last_name")).strip() or None
                break
    publisher = data.get("publisher_name")
    return ResourceRecord(
        id=_text(data.get("id"), "resource_id"), title=_text(data.get("title"), "title"),
        author=author, publisher=publisher if isinstance(publisher, str) else None,
        cover_url=_public_cover(data.get("cover")), media=tuple(media),
    )


def _loan(data: Any, member_id: str) -> LoanRecord:
    if not isinstance(data, Mapping):
        raise BnFPretContractError("invalid_loan")
    if data.get("member_id") != member_id:
        # Never leak one member's loans into another member's snapshot.
        raise BnFPretContractError("loan_identity_mismatch")
    return LoanRecord(
        id=_text(data.get("id"), "loan_id"), member_id=member_id,
        resource_id=_text(data.get("resource_id"), "resource_id"),
        expires_at=_date(data.get("expires_at")),
        hold_expires_at=_date(data.get("hold_expires_at")),
    )


def parse_page(payload: Any, headers: Mapping[str, Any], *, collection: str,
               expected_member_id: str | None = None, status: int = 200) -> Page:
    """Normalize documented JSON + mandatory pagination headers, offline only."""
    if status != 200:
        raise BnFPretContractError(error_code(status, payload), status=status)
    if collection not in {"resources", "loans"}:
        raise BnFPretContractError("unsupported_collection")
    if collection == "loans":
        expected_member_id = _text(expected_member_id, "member_id")
    if not isinstance(payload, Mapping) or not isinstance(payload.get(collection), list):
        raise BnFPretContractError("invalid_payload")
    normalized_headers = {str(key).lower(): value for key, value in headers.items()}
    try:
        current = _number(normalized_headers["x-current-page"], "current_page", minimum=1, maximum=MAX_PAGE)
        pages = _number(normalized_headers["x-total-pages"], "total_pages", maximum=MAX_PAGE)
        total = _number(normalized_headers["x-total-items"], "total_items")
        per_page = _number(normalized_headers["x-per-page"], "per_page", minimum=1, maximum=1000)
    except KeyError as exc:
        raise BnFPretContractError("missing_pagination_headers") from exc
    values = payload[collection]
    if ((pages == 0) != (total == 0) or (pages and current > pages)
            or (pages == 0 and current != 1) or len(values) > per_page
            or len(values) > total or (total > 0 and pages * per_page < total)):
        raise BnFPretContractError("inconsistent_pagination")
    items = tuple(_resource(v) for v in values) if collection == "resources" else tuple(_loan(v, expected_member_id) for v in values)
    return Page(items=items, current=current, total_pages=pages, total_items=total, per_page=per_page)


@dataclass(frozen=True)
class LoanCreationInterpretation:
    # No signed fulfillment URL is retained or made available to callers.
    newly_created: bool
    expires_at: str | None


def interpret_loan_creation(status: int, payload: Any) -> LoanCreationInterpretation:
    """Classify sample *offline* POST responses; this never performs a POST."""
    if status not in {200, 201}:
        raise BnFPretContractError(error_code(status, payload), status=status)
    if not isinstance(payload, Mapping) or not isinstance(payload.get("url"), str):
        raise BnFPretContractError("invalid_loan_response")
    try:
        url = urlsplit(payload["url"])
        safe = (url.scheme == "https" and url.hostname == "pret.bnf.fr"
                and not url.username and not url.password and not url.port
                and url.path.startswith("/loans/"))
    except ValueError:
        safe = False
    if not safe:
        raise BnFPretContractError("invalid_loan_response")
    return LoanCreationInterpretation(newly_created=status == 201, expires_at=_date(payload.get("expires_at")))
