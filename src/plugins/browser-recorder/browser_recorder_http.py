"""Ephemeral, consented HTTP exchange observation for Browser Recorder P1 #2528.

This module NEVER writes HTTP bytes to disk. The previous .har recorder
continues to be metadata-only and is not upgraded to a dangerous raw HAR.
PrivateHttpTrace must be reviewed/redacted (#2529) before any ZIP (#2530).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import re
from threading import Event
from time import monotonic
from urllib.parse import urlsplit

from browser_recorder import RecorderError, _playwright, site_origin

MAX_EXCHANGES = 240
MAX_BODY_BYTES = 32 * 1024
MAX_TOTAL_BODY_BYTES = 768 * 1024
MAX_CAPTURE_SECONDS = 900
_ALLOWED_MIME = frozenset((
    "application/json", "application/xml", "text/xml", "text/html",
    "text/plain", "text/json", "application/x-www-form-urlencoded",
    "application/javascript", "text/javascript",
))
_TECHNICAL_HEADERS = frozenset((
    "content-type", "content-length", "content-encoding", "accept", "accept-language",
    "retry-after", "x-ratelimit-limit", "x-ratelimit-remaining",
    "x-ratelimit-reset", "cache-control", "etag", "last-modified",
))
# These are *never* sampled, even transiently. Other values remain private
# until individually approved, not automatically suitable for publication.
_FORBIDDEN = frozenset((
    "authorization", "proxy-authorization", "cookie", "set-cookie",
    "x-api-key", "x-auth-token", "x-csrf-token", "x-xsrf-token",
))
_HEADER_VALUES = re.compile(r"^[\x20-\x7e]{0,256}$")


def _mime(headers: dict) -> str:
    value = str(headers.get("content-type", "")).lower().split(";", 1)[0].strip()
    if value in _ALLOWED_MIME or re.fullmatch(r"application/[a-z0-9.-]+\+(?:json|xml)", value):
        return value
    return ""


def _headers(source) -> dict[str, str]:
    try:
        raw = source.headers
    except (AttributeError, ValueError):
        return {}
    if type(raw) is not dict:
        return {}
    permitted = {}
    for key, value in raw.items():
        name = str(key).lower()
        if (name in _TECHNICAL_HEADERS and name not in _FORBIDDEN
                and type(value) is str and _HEADER_VALUES.fullmatch(value)):
            permitted[name] = value
    return permitted


def _length(headers: dict[str, str]) -> int | None:
    value = headers.get("content-length", "")
    if not re.fullmatch(r"[0-9]{1,9}", value):
        return None
    return int(value)


def _url_origin(url: str) -> str | None:
    try:
        if not isinstance(url, str):
            return None
        return site_origin(url)
    except RecorderError:
        return None


def _sizes(request) -> dict:
    """Real byte counters returned by Playwright after requestfinished.

    If unavailable, never load response/request bodies: Content-Length alone
    is untrusted and does not bound response.body() memory.
    """
    try:
        sizes = request.sizes()
        return sizes if type(sizes) is dict else {}
    except (AttributeError, TypeError, ValueError):
        return {}


@dataclass(repr=False)
class PrivateExchange:
    sequence: int
    method: str
    url: str = field(repr=False)
    started_ms: int = 0
    status: int = 0
    mime: str = ""
    page_id: int = 0
    request_headers: dict = field(default_factory=dict, repr=False)
    response_headers: dict = field(default_factory=dict, repr=False)
    request_body: str | None = field(default=None, repr=False)
    response_body: str | None = field(default=None, repr=False)
    request_body_state: str = "body_omitted"
    response_body_state: str = "body_omitted"
    redirect_to: str | None = field(default=None, repr=False)
    network_error: bool = False

    def __repr__(self) -> str:
        # A debugging object representation must never leak its secret URL,
        # query, headers or bodies.
        return (f"PrivateExchange(sequence={self.sequence}, method={self.method!r}, "
                f"status={self.status}, request_body_state={self.request_body_state!r}, "
                f"response_body_state={self.response_body_state!r})")


class PrivateHttpTrace:
    """Raw/private in-memory observations, never serializable without review."""

    def __init__(self, origins: tuple[str, ...], *,
                 limit: int = MAX_EXCHANGES,
                 max_body: int = MAX_BODY_BYTES,
                 total_body: int = MAX_TOTAL_BODY_BYTES):
        if not origins or len(origins) > 6:
            raise RecorderError("invalid_origin")
        self.origins = tuple(dict.fromkeys(site_origin(item) for item in origins))
        self.limit = min(MAX_EXCHANGES, max(1, int(limit)))
        self.max_body = min(MAX_BODY_BYTES, max(1, int(max_body)))
        self.total_body = min(MAX_TOTAL_BODY_BYTES, max(0, int(total_body)))
        self.exchanges: list[PrivateExchange] = []
        self.omitted = {"out_of_scope": 0, "limit": 0, "binary_skipped": 0,
                        "oversize": 0, "unknown_size": 0, "network_error": 0}
        self._pending: dict[int, PrivateExchange] = {}
        self._responses: dict[int, object] = {}
        self._begin = monotonic()

    def __repr__(self):
        return (f"PrivateHttpTrace(exchanges={len(self.exchanges)}, "
                f"omissions={sum(self.omitted.values())})")

    def _take_text(self, raw: bytes) -> str | None:
        if len(raw) > self.max_body or len(raw) > self.total_body:
            return None
        try:
            decoded = raw.decode("utf-8", errors="strict")
        except UnicodeError:
            return None
        self.total_body -= len(raw)
        return decoded

    def request(self, req) -> None:
        try:
            origin = _url_origin(req.url)
            if origin not in self.origins:
                self.omitted["out_of_scope"] += 1
                return
            if len(self.exchanges) >= self.limit:
                self.omitted["limit"] += 1
                return
            method = str(req.method).upper()
            if method not in {"GET", "HEAD", "OPTIONS", "POST", "PUT",
                              "PATCH", "DELETE"}:
                method = "OTHER"
            entry = PrivateExchange(
                sequence=len(self.exchanges) + 1, method=method,
                url=str(req.url),
                started_ms=int((monotonic() - self._begin) * 1000),
                request_headers=_headers(req),
            )
            self.exchanges.append(entry)
            self._pending[id(req)] = entry
        except (AttributeError, TypeError, ValueError):
            self.omitted["network_error"] += 1

    def response(self, resp) -> None:
        try:
            req = resp.request
            entry = self._pending.get(id(req))
            if entry is None:
                return
            status = int(resp.status)
            entry.status = status if 100 <= status <= 599 else 0
            entry.response_headers = _headers(resp)
            entry.mime = _mime(entry.response_headers)
            entry.response_body_state = (
                "body_omitted" if not entry.mime else "pending"
            )
            # Location is private and only retained in the ephemeral trace.
            # It must later pass the same ID/secret review as the request URL.
            if 300 <= entry.status <= 399:
                try:
                    location = resp.headers.get("location", "")
                    if isinstance(location, str) and len(location) <= 2048:
                        entry.redirect_to = location
                except (AttributeError, TypeError, ValueError):
                    pass
                entry.response_body_state = "redirect"
            self._responses[id(req)] = resp
        except (TypeError, AttributeError, ValueError):
            self.omitted["network_error"] += 1

    def finished(self, req) -> None:
        key = id(req)
        entry = self._pending.pop(key, None)
        resp = self._responses.pop(key, None)
        if entry is None:
            return
        sizes = _sizes(req)
        request_length = _length(entry.request_headers)
        actual_request = sizes.get("requestBodySize")
        if entry.method in {"POST", "PUT", "PATCH"}:
            if (request_length is None or not isinstance(actual_request, int)
                    or request_length > self.max_body
                    or not 0 <= actual_request <= self.max_body
                    or self.total_body < actual_request):
                entry.request_body_state = "body_omitted"
                self.omitted["unknown_size"] += 1
            elif not _mime(entry.request_headers):
                entry.request_body_state = "binary_skipped"
                self.omitted["binary_skipped"] += 1
            else:
                try:
                    # Reading post_data_buffer is allowed only after *both*
                    # declared and actual byte-size checks.
                    raw = req.post_data_buffer
                    if type(raw) is bytes and len(raw) == actual_request:
                        entry.request_body = self._take_text(raw)
                        entry.request_body_state = (
                            "captured" if entry.request_body is not None
                            else "body_omitted"
                        )
                    else:
                        entry.request_body_state = "body_omitted"
                except (AttributeError, TypeError, ValueError):
                    entry.request_body_state = "body_omitted"
        if resp is None or entry.response_body_state == "redirect":
            return
        if not entry.mime:
            entry.response_body_state = "binary_skipped"
            self.omitted["binary_skipped"] += 1
            return
        headers = entry.response_headers
        # No compressed responses: Content-Length may describe only *wire*
        # bytes and a malicious compressed body could inflate unboundedly.
        if headers.get("content-encoding", "").lower() not in ("", "identity"):
            entry.response_body_state = "body_omitted"
            self.omitted["unknown_size"] += 1
            return
        expected = _length(headers)
        actual = sizes.get("responseBodySize")
        if (expected is None or type(actual) is not int or
                not 0 <= actual <= self.max_body or
                expected > self.max_body or actual > self.total_body):
            entry.response_body_state = "body_omitted"
            self.omitted["unknown_size"] += 1
            return
        try:
            raw = resp.body()
            if type(raw) is bytes and len(raw) <= actual and len(raw) <= expected:
                entry.response_body = self._take_text(raw)
                entry.response_body_state = (
                    "captured" if entry.response_body is not None else "body_omitted"
                )
            else:
                entry.response_body_state = "truncated"
                self.omitted["oversize"] += 1
        except (AttributeError, TypeError, ValueError):
            entry.response_body_state = "body_omitted"

    def failed(self, req) -> None:
        entry = self._pending.pop(id(req), None)
        self._responses.pop(id(req), None)
        if entry is not None:
            entry.network_error = True
            entry.response_body_state = "network_error"
            self.omitted["network_error"] += 1

    def destroy(self) -> None:
        """Discard all values in memory; do not create a local capture."""
        for entry in self.exchanges:
            entry.url = ""
            entry.redirect_to = None
            entry.request_body = None
            entry.response_body = None
            entry.request_headers.clear()
            entry.response_headers.clear()
        self.exchanges.clear()
        self._pending.clear()
        self._responses.clear()


def record_http(start_url: str, stop: Event, *,
                origins: tuple[str, ...] | None = None,
                status=None, factory=None, max_seconds: int = MAX_CAPTURE_SECONDS
                ) -> PrivateHttpTrace:
    """Observe a manually controlled Chromium session; return RAM-only trace.

    No HAR, ZIP, storage state or body is ever written. No extra network
    requests or transaction replay. The caller must destroy the trace if
    review is abandoned; export requires a separate explicit confirmation.
    """
    origin = site_origin(start_url)
    trace = PrivateHttpTrace(origins or (origin,))
    if origin not in trace.origins:
        raise RecorderError("invalid_origin")
    try:
        with _playwright(factory) as pw:
            browser = pw.chromium.launch(headless=False)
            try:
                context = browser.new_context(
                    accept_downloads=False, service_workers="block",
                )
                try:
                    context.on("request", trace.request)
                    context.on("response", trace.response)
                    context.on("requestfinished", trace.finished)
                    context.on("requestfailed", trace.failed)
                    page = context.new_page()
                    page.goto(start_url, wait_until="domcontentloaded", timeout=30000)
                    if status:
                        status("capture_ready")
                    end = monotonic() + min(MAX_CAPTURE_SECONDS, max(1, int(max_seconds)))
                    while not stop.is_set() and monotonic() < end:
                        page.wait_for_timeout(200)
                finally:
                    context.close()
            finally:
                browser.close()
        if not stop.is_set():
            raise RecorderError("capture_interrupted")
        return trace
    except Exception:
        trace.destroy()
        raise
