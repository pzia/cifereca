"""Ephemeral, consented HTTP exchange observation for Browser Recorder P1 #2528.

This module NEVER writes HTTP bytes to disk. The previous .har recorder
continues to be metadata-only and is not upgraded to a dangerous raw HAR.
PrivateHttpTrace must be reviewed/redacted (#2529) before any ZIP (#2530).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import re
from threading import Event, Lock
from time import monotonic
from urllib.parse import urlsplit

from browser_recorder import RecorderError, _playwright, site_origin
from browser_recorder_download import (
    MAX_MANUAL_DOWNLOADS, is_download_response, save_chosen_download,
)

MAX_EXCHANGES = 240
DEFAULT_BODY_BYTES = 32 * 1024
MAX_BODY_BYTES = 128 * 1024
BODY_LIMIT_CHOICES = frozenset((DEFAULT_BODY_BYTES, 64 * 1024, MAX_BODY_BYTES))
MAX_TOTAL_BODY_BYTES = 768 * 1024
MAX_CAPTURE_SECONDS = 900
MAX_STATIC_METADATA = 64
# Closed, source-independent codes; safe to persist in the encrypted vault
# and export as diagnostic provenance without revealing URL/header/body.
_BODY_REASONS = frozenset((
    "unknown", "not_applicable", "not_observed", "metadata_only", "pending",
    "captured", "redirect", "network_error", "unfinished", "compressed",
    "unsafe_headers", "size_unavailable", "over_budget", "unsupported_mime",
    "measured_mismatch", "text_invalid_or_limit", "read_failed",
))
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


def _body_envelope(response, *, allow_compressed: bool = False) -> tuple[str, int | None]:
    """Read only raw wire-size/encoding headers transiently before body().

    Sanitized technical headers are safe to *store* but cannot prove that
    Content-Encoding or Content-Length was absent: a malformed header may
    have been discarded by _headers(). Such a response must fail closed.
    Values are never retained, logged, or serialized.
    """
    try:
        raw = response.headers
    except (AttributeError, TypeError, ValueError):
        return "unknown_size", None
    if type(raw) is not dict:
        return "unknown_size", None
    length = None
    saw_length = False
    saw_encoding = False
    compressed = False
    # Inspect *names* first. Access values only for the two technical
    # headers below; do not even sample Cookie/Authorization in this gate.
    for key in raw:
        if type(key) is not str:
            return "unknown_size", None
        name = key.lower()
        if name == "content-encoding":
            if saw_encoding:
                return "compressed", None
            saw_encoding = True
            value = raw[key]
            if type(value) is not str:
                return "compressed", None
            coding = value.strip().lower()
            if coding != "identity":
                # No stacked/unknown encodings, no accidental opt-in via a
                # malformed header. Chromium returns decoded response.body().
                if not allow_compressed or coding not in {"gzip", "br", "deflate"}:
                    return "compressed", None
                compressed = True
        elif name == "content-length":
            if saw_length:
                return "unknown_size", None
            saw_length = True
            value = raw[key]
            if type(value) is not str:
                return "unknown_size", None
            length = _length({"content-length": value})
            if length is None:
                return "unknown_size", None
    return ("compressed_ready" if compressed else "ready"), length


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


class CapturePause:
    """Thread-safe pause control, without captured network data."""

    def __init__(self):
        self._lock = Lock()
        self._paused = False
        self._revision = 0

    def set_paused(self, value: bool) -> None:
        with self._lock:
            value = bool(value)
            if value != self._paused:
                self._paused = value
                self._revision += 1

    def state(self) -> tuple[bool, int]:
        with self._lock:
            return self._paused, self._revision


@dataclass(repr=False)
class PrivateExchange:
    sequence: int
    method: str
    url: str = field(repr=False)
    started_ms: int = 0
    duration_ms: int = 0
    status: int = 0
    mime: str = ""
    page_id: int = 0
    request_headers: dict = field(default_factory=dict, repr=False)
    response_headers: dict = field(default_factory=dict, repr=False)
    request_body: str | None = field(default=None, repr=False)
    response_body: str | None = field(default=None, repr=False)
    request_body_state: str = "body_omitted"
    response_body_state: str = "body_omitted"
    request_body_reason: str = "unknown"
    response_body_reason: str = "unknown"
    redirect_to: str | None = field(default=None, repr=False)
    network_error: bool = False
    # Optional third-party context, never a URL, header, MIME or body.
    metadata_only: bool = False

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
                 max_body: int = DEFAULT_BODY_BYTES,
                 total_body: int = MAX_TOTAL_BODY_BYTES,
                 pause_control: CapturePause | None = None,
                 metadata_only_origins: tuple[str, ...] = (),
                 diagnose_origins: bool = False,
                 allow_compressed_responses: bool = False):
        if type(allow_compressed_responses) is not bool:
            raise RecorderError("invalid_capture_strategy")
        self.allow_compressed_responses = allow_compressed_responses
        if type(diagnose_origins) is not bool:
            raise RecorderError("invalid_origin")
        self._diagnose_origins = diagnose_origins
        self._excluded_origins = {}
        self._excluded_origin_overflow = 0
        if not origins or len(origins) > 6:
            raise RecorderError("invalid_origin")
        self.origins = tuple(dict.fromkeys(site_origin(item) for item in origins))
        if (type(metadata_only_origins) not in (tuple, list)
                or len(metadata_only_origins) > 4):
            raise RecorderError("invalid_origin")
        self.metadata_only_origins = tuple(dict.fromkeys(
            site_origin(item) for item in metadata_only_origins
            if site_origin(item) not in self.origins
        ))
        self.limit = min(MAX_EXCHANGES, max(1, int(limit)))
        self.max_body = min(MAX_BODY_BYTES, max(1, int(max_body)))
        self.total_body = min(MAX_TOTAL_BODY_BYTES, max(0, int(total_body)))
        self._static_body_remaining = min(64 * 1024, self.total_body // 4)
        self.exchanges: list[PrivateExchange] = []
        # Closed vocabulary only: no provider value or browser exception.
        self.end_reason = "button"
        self.content_negotiation = "browser_default"
        self.omitted = {"out_of_scope": 0, "limit": 0, "binary_skipped": 0,
                        "oversize": 0, "unknown_size": 0, "compressed": 0,
                        "network_error": 0, "paused": 0, "unfinished": 0}
        self.omitted["static_metadata_budget"] = 0
        self._static_metadata = 0
        self._pending: dict[int, PrivateExchange] = {}
        self._responses: dict[int, object] = {}
        self._begin = monotonic()
        self.paused = False
        self._pause_control = pause_control
        self._pause_revision = pause_control.state()[1] if pause_control else 0
        self._tab_numbers: dict[int, int] = {}

    def __repr__(self):
        return (f"PrivateHttpTrace(exchanges={len(self.exchanges)}, "
                f"omissions={sum(self.omitted.values())})")

    def origin_diagnostics(self):
        """Private opt-in inventory, RAM only; not evidence of API necessity.

        The vault/ZIP serializers intentionally never consume this inventory.
        Only normalized origins, at most 16, with no path/query/header/body.
        """
        return dict(self._excluded_origins), self._excluded_origin_overflow

    def coverage(self):
        """Closed numeric summary; never a URL, MIME or provider payload."""
        return {
            "exchanges": len(self.exchanges), "limit": self.limit,
            "max_body": self.max_body,
            "text_responses": sum(type(e.response_body) is str for e in self.exchanges),
            "binary_skipped": self.omitted["binary_skipped"],
            "compressed": self.omitted["compressed"],
            "out_of_scope": self.omitted["out_of_scope"],
            "static_metadata_budget": self.omitted["static_metadata_budget"],
        }

    def _drop_inflight_on_pause(self) -> None:
        # A pause cannot finish capturing a request that began earlier.
        self.omitted["paused"] += len(self._pending)
        for entry in self._pending.values():
            entry.request_body_state = "body_omitted"
            entry.response_body_state = "body_omitted"
        self._pending.clear()
        self._responses.clear()

    def _is_paused(self) -> bool:
        if self._pause_control is not None:
            paused, revision = self._pause_control.state()
            if revision != self._pause_revision:
                # Detect pause + resume between two Playwright callbacks.
                self._drop_inflight_on_pause()
                self._pause_revision = revision
            return paused
        return self.paused

    def pause(self) -> None:
        if not self.paused:
            self.paused = True
            self._drop_inflight_on_pause()

    def resume(self) -> None:
        self.paused = False

    def _tab(self, request) -> int:
        # Request.frame is unavailable during certain navigation requests;
        # this is diagnostic only and never changes the capture's security.
        try:
            page = request.frame.page
        except Exception:
            return 0
        key = id(page)
        if key not in self._tab_numbers:
            self._tab_numbers[key] = len(self._tab_numbers) + 1
        return self._tab_numbers[key]

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
        if self._is_paused():
            self.omitted["paused"] += 1
            return
        try:
            origin = _url_origin(req.url)
            metadata_only = origin in self.metadata_only_origins
            if origin not in self.origins and not metadata_only:
                self.omitted["out_of_scope"] += 1
                if self._diagnose_origins and origin is not None:
                    if origin in self._excluded_origins:
                        self._excluded_origins[origin] = min(1_000_000, self._excluded_origins[origin] + 1)
                    elif len(self._excluded_origins) < 16:
                        self._excluded_origins[origin] = 1
                    else:
                        self._excluded_origin_overflow = min(1_000_000, self._excluded_origin_overflow + 1)
                return
            # Browser-declared request intent, not a claim about final MIME.
            # Keep a bounded sample of static-resource chronology. Never
            # discard POSTs or classify xhr/fetch/documents as static assets.
            static = (req.method == "GET" and getattr(req, "resource_type", None)
                      in {"image", "font", "media", "stylesheet", "script"})
            if static and self._static_metadata >= min(MAX_STATIC_METADATA, self.limit // 3):
                self.omitted["static_metadata_budget"] += 1
                return
            if len(self.exchanges) >= self.limit:
                self.omitted["limit"] += 1
                return
            if static:
                self._static_metadata += 1
            method = str(req.method).upper()
            if method not in {"GET", "HEAD", "OPTIONS", "POST", "PUT",
                              "PATCH", "DELETE"}:
                method = "OTHER"
            entry = PrivateExchange(
                sequence=len(self.exchanges) + 1, method=method,
                # Never retain the target host/path/query of a third party.
                url="" if metadata_only else str(req.url),
                started_ms=int((monotonic() - self._begin) * 1000),
                page_id=self._tab(req),
                request_headers={} if metadata_only else _headers(req),
                metadata_only=metadata_only,
                request_body_reason=(
                    "metadata_only" if metadata_only else
                    "pending" if method in {"POST", "PUT", "PATCH"}
                    else "not_applicable"
                ),
                response_body_reason=(
                    "metadata_only" if metadata_only else "not_observed"
                ),
            )
            self.exchanges.append(entry)
            self._pending[id(req)] = entry
        except (AttributeError, TypeError, ValueError):
            self.omitted["network_error"] += 1

    def response(self, resp) -> None:
        if self._is_paused():
            return
        try:
            req = resp.request
            entry = self._pending.get(id(req))
            if entry is None:
                return
            status = int(resp.status)
            entry.status = status if 100 <= status <= 599 else 0
            if entry.metadata_only:
                # No response object retained and no header, Location, MIME
                # or body access, even for a redirect/authentication failure.
                return
            entry.response_headers = _headers(resp)
            # Attachment/license/download bodies are never held in RAM,
            # regardless of their advertised XML/JSON MIME or sizes.
            entry.mime = ("" if is_download_response(resp)
                          else _mime(entry.response_headers))
            entry.response_body_state = (
                "body_omitted" if not entry.mime else "pending"
            )
            entry.response_body_reason = (
                "unsupported_mime" if not entry.mime else "pending"
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
                entry.response_body_reason = "redirect"
            self._responses[id(req)] = resp
        except (TypeError, AttributeError, ValueError):
            self.omitted["network_error"] += 1

    def finished(self, req) -> None:
        if self._is_paused():
            return
        key = id(req)
        entry = self._pending.pop(key, None)
        resp = self._responses.pop(key, None)
        if entry is None:
            return
        entry.duration_ms = max(0, int((monotonic() - self._begin) * 1000)
                                - entry.started_ms)
        if entry.metadata_only:
            return
        sizes = _sizes(req)
        actual_request = sizes.get("requestBodySize")
        if entry.method in {"POST", "PUT", "PATCH"}:
            # Request post_data_buffer may hold private data. Just like a
            # response, its raw encoding/declared length MUST be validated
            # before it is read; Chromium's measured payload size is the
            # controlling upper bound for chunked or ordinary POST bodies.
            request_gate, request_length = _body_envelope(req)
            if request_gate != "ready":
                entry.request_body_state = "body_omitted"
                entry.request_body_reason = (
                    "compressed" if request_gate == "compressed" else "unsafe_headers"
                )
                self.omitted[
                    "compressed" if request_gate == "compressed" else "unknown_size"
                ] += 1
            elif type(actual_request) is not int or actual_request <= 0:
                entry.request_body_state = "body_omitted"
                entry.request_body_reason = "size_unavailable"
                self.omitted["unknown_size"] += 1
            elif (actual_request > self.max_body
                    or actual_request > self.total_body
                    or (request_length is not None and request_length > self.max_body)):
                entry.request_body_state = "body_omitted"
                entry.request_body_reason = "over_budget"
                self.omitted["oversize"] += 1
            elif not _mime(entry.request_headers):
                entry.request_body_state = "binary_skipped"
                entry.request_body_reason = "unsupported_mime"
                self.omitted["binary_skipped"] += 1
            else:
                try:
                    raw = req.post_data_buffer
                    if (type(raw) is bytes and len(raw) == actual_request
                            and (request_length is None
                                 or len(raw) <= request_length)):
                        entry.request_body = self._take_text(raw)
                        entry.request_body_state = (
                            "captured" if entry.request_body is not None
                            else "body_omitted"
                        )
                        entry.request_body_reason = (
                            "captured" if entry.request_body is not None
                            else "text_invalid_or_limit"
                        )
                    else:
                        entry.request_body_state = "body_omitted"
                        entry.request_body_reason = "measured_mismatch"
                        self.omitted["oversize"] += 1
                except (AttributeError, TypeError, ValueError):
                    entry.request_body_state = "body_omitted"
                    entry.request_body_reason = "read_failed"
        if resp is None or entry.response_body_state == "redirect":
            return
        if not entry.mime:
            entry.response_body_state = "binary_skipped"
            entry.response_body_reason = "unsupported_mime"
            self.omitted["binary_skipped"] += 1
            return
        # Examine original headers transiently: storage sanitization can
        # drop malformed Content-Encoding/Content-Length. Never mistake that
        # filtering for a trustworthy 'header absent' condition.
        gate, expected = _body_envelope(
            resp, allow_compressed=self.allow_compressed_responses,
        )
        compressed = gate == "compressed_ready"
        if gate not in {"ready", "compressed_ready"}:
            entry.response_body_state = "body_omitted"
            entry.response_body_reason = (
                "compressed" if gate == "compressed" else "unsafe_headers"
            )
            self.omitted["compressed" if gate == "compressed" else "unknown_size"] += 1
            return
        # An *absent* Content-Length is normal for HTTP/2 and chunked HTTP/1.1:
        # Chromium's already-measured, encoded responseBodySize is an
        # independent pre-read bound when the original transfer is identity.
        actual = sizes.get("responseBodySize")
        if type(actual) is not int or actual <= 0:
            entry.response_body_state = "body_omitted"
            entry.response_body_reason = "size_unavailable"
            self.omitted["unknown_size"] += 1
            return
        static = (entry.method == "GET" and getattr(req, "resource_type", None)
                  in {"image", "font", "media", "stylesheet", "script"})
        if (actual > self.max_body or actual > self.total_body
                or (static and actual > self._static_body_remaining)
                or (expected is not None and expected > self.max_body)):
            entry.response_body_state = "body_omitted"
            entry.response_body_reason = "over_budget"
            self.omitted["oversize"] += 1
            return
        try:
            # The opt-in permits Chromium's decompressed body() after a
            # measured *encoded* transfer-size preflight. This is NOT a bound
            # on peak decoded memory: users explicitly accept that residual
            # risk. Never retain more than the normal decoded budgets.
            # No retries, replay, other origins, or binary/license reads.
            raw = resp.body()
            if type(raw) is not bytes:
                entry.response_body_state = "body_omitted"
                entry.response_body_reason = "measured_mismatch"
                self.omitted["oversize"] += 1
            elif (len(raw) > self.max_body or len(raw) > self.total_body
                  or (static and len(raw) > self._static_body_remaining)):
                entry.response_body_state = "body_omitted"
                entry.response_body_reason = "over_budget"
                self.omitted["oversize"] += 1
            elif not compressed and (len(raw) > actual
                    or (expected is not None and len(raw) > expected)):
                entry.response_body_state = "truncated"
                entry.response_body_reason = "measured_mismatch"
                self.omitted["oversize"] += 1
            else:
                entry.response_body = self._take_text(raw)
                if static and entry.response_body is not None:
                    self._static_body_remaining -= len(raw)
                entry.response_body_state = (
                    "captured" if entry.response_body is not None else "body_omitted"
                )
                entry.response_body_reason = (
                    "captured" if entry.response_body is not None
                    else "text_invalid_or_limit"
                )
        except (AttributeError, TypeError, ValueError):
            entry.response_body_state = "body_omitted"
            entry.response_body_reason = "read_failed"

    def failed(self, req) -> None:
        if self._is_paused():
            return
        entry = self._pending.pop(id(req), None)
        self._responses.pop(id(req), None)
        if entry is not None:
            entry.duration_ms = max(0, int((monotonic() - self._begin) * 1000)
                                    - entry.started_ms)
            entry.network_error = True
            entry.response_body_state = "network_error"
            entry.response_body_reason = "network_error"
            self.omitted["network_error"] += 1

    def finish_open_requests(self) -> None:
        """Seal outstanding requests on a *normal* stop without inventing HTTP.

        Pending responses are not read. No Playwright object may survive in
        the resulting review, and neither paused nor abandoned bodies leak.
        """
        now_ms = int((monotonic() - self._begin) * 1000)
        self.omitted["unfinished"] += len(self._pending)
        for entry in self._pending.values():
            entry.duration_ms = max(0, now_ms - entry.started_ms)
            entry.request_body = None
            entry.response_body = None
            entry.request_body_state = "body_omitted"
            entry.request_body_reason = "unfinished"
            if entry.response_body_state != "redirect":
                entry.response_body_state = "unfinished"
                entry.response_body_reason = "unfinished"
        self._pending.clear()
        self._responses.clear()

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
        self._tab_numbers.clear()
        self._excluded_origins.clear()
        self._excluded_origin_overflow = 0


def record_http(start_url: str, stop: Event, *,
                origins: tuple[str, ...] | None = None,
                status=None, factory=None, max_seconds: int = MAX_CAPTURE_SECONDS,
                pause_control: CapturePause | None = None,
                metadata_only_origins: tuple[str, ...] = (),
                vault_writer=None,
                download_destination=None,
                coverage_callback=None,
                prefer_identity: bool = False,
                diagnose_origins: bool = False,
                allow_compressed_responses: bool = False,
                max_body: int = DEFAULT_BODY_BYTES,
                ) -> PrivateHttpTrace:
    """Observe a manually controlled Chromium session; return RAM-only trace.

    No HAR, ZIP or browser profile is written. Only an explicitly enabled
    private vault writer may persist AES-GCM-encrypted snapshots, never
    plaintext. No extra requests or replay; LLM ZIP still requires review.
    """
    if (type(prefer_identity) is not bool or
            type(allow_compressed_responses) is not bool or
            type(max_body) is not int or max_body not in BODY_LIMIT_CHOICES):
        raise RecorderError("invalid_capture_strategy")
    origin = site_origin(start_url)
    trace = PrivateHttpTrace(
        origins or (origin,), pause_control=pause_control,
        metadata_only_origins=metadata_only_origins,
        diagnose_origins=diagnose_origins,
        allow_compressed_responses=allow_compressed_responses,
        max_body=max_body,
    )
    if origin not in trace.origins:
        raise RecorderError("invalid_origin")
    trace.content_negotiation = "identity_requested" if prefer_identity else "browser_default"
    try:
        if vault_writer is not None:
            vault_writer.save(trace, "recording", force=True)
        reason = None
        with _playwright(factory) as pw:
            browser = pw.chromium.launch(headless=False)
            try:
                options = {"accept_downloads": True, "service_workers": "block"}
                if prefer_identity:
                    # Explicit representation negotiation, not replay or a
                    # decoder. Servers may ignore it: the original compressed
                    # body guard below MUST still reject those responses.
                    options["extra_http_headers"] = {"Accept-Encoding": "identity"}
                context = browser.new_context(**options)
                try:
                    last_page_closed = Event()
                    crashed = Event()
                    tracked_pages = set()
                    downloads = []  # Playwright-only objects; never serialized.

                    def track_page(opened):
                        # A tab can be closed without closing the Chromium
                        # session. Observe *all* tabs, including new popups.
                        if id(opened) in tracked_pages:
                            return
                        tracked_pages.add(id(opened))
                        last_page_closed.clear()
                        opened.on("crash", lambda *_: crashed.set())

                        def on_download(download):
                            if len(downloads) < MAX_MANUAL_DOWNLOADS:
                                downloads.append(download)
                                if status:
                                    status("download_started")
                            elif status:
                                status("download_limit")

                        opened.on("download", on_download)

                        def on_close(*_):
                            try:
                                if not any(not item.is_closed() for item in context.pages):
                                    last_page_closed.set()
                            except Exception:
                                # A disconnected browser without a verified
                                # last-page close is a failure, never consent.
                                pass

                        opened.on("close", on_close)

                    context.on("page", track_page)
                    context.on("request", trace.request)
                    context.on("response", trace.response)
                    context.on("requestfinished", trace.finished)
                    context.on("requestfailed", trace.failed)
                    page = context.new_page()
                    track_page(page)
                    try:
                        page.goto(
                            start_url, wait_until="domcontentloaded", timeout=30000,
                        )
                    except Exception:
                        # Closing the last window during navigation can raise
                        # TargetClosed; do not swallow unrelated navigation
                        # failures, missing pages or crashes.
                        if crashed.is_set() or not last_page_closed.is_set():
                            raise
                    if status and not last_page_closed.is_set():
                        status("capture_ready")
                    if coverage_callback:
                        coverage_callback(trace.coverage())
                    end = monotonic() + min(
                        MAX_CAPTURE_SECONDS, max(1, int(max_seconds)),
                    )
                    while True:
                        if crashed.is_set():
                            raise RecorderError("capture_interrupted")
                        if stop.is_set():
                            reason = "button"
                            break
                        if last_page_closed.is_set():
                            reason = "browser_closed"
                            break
                        if monotonic() >= end:
                            raise RecorderError("capture_interrupted")
                        connected = getattr(browser, "is_connected", None)
                        if callable(connected) and not connected():
                            raise RecorderError("capture_interrupted")
                        # Waiting on the original page after it was closed
                        # aborts multi-tab sessions. Prefer a surviving tab.
                        live_pages = [item for item in context.pages
                                      if not item.is_closed()]
                        if not live_pages:
                            raise RecorderError("capture_interrupted")
                        try:
                            live_pages[0].wait_for_timeout(200)
                            if coverage_callback:
                                coverage_callback(trace.coverage())
                            if vault_writer is not None:
                                vault_writer.save(trace)
                        except Exception:
                            if crashed.is_set():
                                raise RecorderError("capture_interrupted") from None
                            # A user may close the first tab while a popup
                            # stays open. That page's timeout raises
                            # TargetClosed, but the capture must continue on
                            # the surviving tab (not swallow unrelated errors).
                            other_tab_survives = (
                                live_pages[0].is_closed()
                                and any(not tab.is_closed() for tab in context.pages)
                            )
                            if not (stop.is_set() or last_page_closed.is_set()
                                    or other_tab_survives):
                                raise
                            # The next iteration settles Stop vs last-page X
                            # exactly once, rather than losing the RAM trace.
                    # Keep the browser context alive until optional *human*
                    # file choices finish. Without a callback, Playwright's
                    # temporary downloads are discarded on context.close().
                    for number, download in enumerate(downloads, 1):
                        if download_destination is None:
                            continue
                        try:
                            chosen = download_destination(number)
                        except Exception:
                            chosen = None
                        if chosen is None:
                            if status:
                                status("download_cancelled")
                            continue
                        try:
                            save_chosen_download(download, chosen)
                        except Exception:
                            if status:
                                status("download_failed")
                        else:
                            if status:
                                status("download_saved_by_user")
                finally:
                    try:
                        context.close()
                    except Exception:
                        # Cleanup after a user-closed window may itself yield
                        # TargetClosed; never override a completed capture.
                        pass
            finally:
                try:
                    browser.close()
                except Exception:
                    pass
        trace.finish_open_requests()
        trace.end_reason = reason
        if vault_writer is not None:
            vault_writer.save(trace, "completed", force=True)
        return trace
    except Exception:
        if vault_writer is not None:
            try:
                trace.finish_open_requests()
                vault_writer.save(trace, "interrupted", force=True)
            except Exception:
                # The last 'recording' encrypted snapshot remains recoverable.
                pass
        trace.destroy()
        raise
