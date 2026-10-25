"""Bounded, fail-closed multi-page selection of *redacted* Core diagnostics.

No filesystem access, private provider state, tokens or synchronous GTK work.
Until Core exposes an atomic paginated snapshot, repeat the first-page
fingerprint and label the result as a non-atomic view of published events.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import re

from cifereca.plugin_api import ProviderError
from diagnostics_explorer import (
    PAGE_SIZE, PERIODS, SEVERITIES, SOURCES, export_selected_events,
    explorer_since, fetch_explorer_page, next_page_offset, sanitized_rows, selectable_event_ids,
    source_summaries, public_provider_id, normalize_diagnostic_domain,
)

MAX_SELECTED = 200
MAX_SCAN_PAGES = 8
MAX_EXPORT_BYTES = 96_000
# Core publishes only these domain names for an exact parsed app_log TRACE.
# These are metadata for internal scope verification, never exported as
# additional arbitrary provider content.
_ATTESTED_LOG_DOMAINS = frozenset({"KOBO", "NUMILOG", "ADEPT", "SYNC", "PLUGIN"})


@dataclass(frozen=True)
class FilteredSelection:
    """A frozen, expurgated export, never a cache of private account rights."""

    event_ids: frozenset[str]
    export_json: str
    count: int
    inspected: int
    partial_sources: bool


def page_selection_ids(rows: list[dict], previous: set[str], action: str) -> set[str]:
    """Quick operations apply ONLY to uniquely identified visible rows."""
    if action not in {"errors", "all", "invert", "clear"}:
        raise ValueError("invalid selection action")
    valid = selectable_event_ids(rows)
    checked = previous & valid
    if action == "clear":
        return set()
    if action == "all":
        return valid
    if action == "invert":
        return valid - checked
    return {row["event_id"] for row in rows
            if row.get("event_id") in valid
            and row.get("severity") in ("error", "critical")}


# These seven fields are the complete exported, redacted event summary.
# Rechecking their fingerprint (not only IDs) prevents stale message/status
# changes being advertised as a stable, selected snapshot.
_EXPORTED_EVENT_FIELDS = (
    "timestamp", "source", "module", "severity", "message", "status", "event_id",
)


def _redacted_page_fingerprint(rows):
    # Keep the closed business domain in the fingerprint even though it is
    # intentionally absent from the clipboard JSON. A changing classification
    # must invalidate a multi-page selection.
    return tuple(
        tuple(row[field] for field in _EXPORTED_EVENT_FIELDS)
        + (row.get("business_module", ""), row.get("provider_id", ""),
           row.get("domain", ""))
        for row in rows
    )


def _check_selected_scope(rows, *, source: str, module: str, severity: str,
                          since: datetime | None, reason_code: str = "",
                          provider_id: str = "", domain: str = ""):
    """Reject all events contradicting a requested Core broker filter.

    A malformed/old timestamp must not count as "all errors this hour".
    The upper bound is intentionally absent, as in Core's actual since-only
    query; do not invent a future-time filter that Core never requested.
    """
    module_prefix = module.strip().casefold()
    for row in rows:
        if (row["source"] not in ("app_log", "core_events", "journal")
                or (source != "all" and row["source"] != source)
                or row["severity"] not in ("debug", "info", "warning", "error", "critical")
                or (severity and row["severity"] != severity)
                or (module_prefix
                    and not row["module"].casefold().startswith(module_prefix)
                    and not row.get("business_module", "").casefold().startswith(module_prefix))):
            raise ValueError("diagnostic selection contains out-of-scope events")
        if provider_id and source != "journal" and row.get("provider_id") != provider_id:
            raise ValueError("diagnostic selection contains out-of-scope events")
        if domain and row.get("domain") != domain:
            raise ValueError("diagnostic selection contains out-of-scope events")
        if reason_code:
            detail = row.get("detail")
            if (row["source"] != "core_events" or row["module"] != "LIBRARY"
                    or not isinstance(detail, Mapping)
                    or detail.get("operation") != "library_reconciliation_audit"
                    or type(detail.get("reasons")) is not list
                    or reason_code not in detail["reasons"]):
                raise ValueError("diagnostic selection contains out-of-scope events")
        if since is not None:
            try:
                timestamp = datetime.fromisoformat(
                    row["timestamp"].replace("Z", "+00:00"),
                )
                if timestamp.tzinfo is None or timestamp.astimezone(timezone.utc) < since:
                    raise ValueError("out of range")
            except (ValueError, OverflowError, TypeError):
                # No text from an invalid timestamp crosses the UI, report,
                # clipboard or task failure boundary.
                raise ValueError("diagnostic selection contains out-of-period events") from None


def _checked_page(payload: Mapping, offset: int, expected_total: int | None,
                  *, reason_code: str = ""):
    if not isinstance(payload, Mapping):
        raise ValueError("diagnostic selection unavailable")
    total = payload.get("total")
    received = payload.get("offset")
    items = payload.get("events")
    if (type(total) is not int or not 0 <= total <= 100000
            or type(received) is not int or received != offset
            or not isinstance(items, (list, tuple)) or len(items) > PAGE_SIZE
            or (expected_total is not None and total != expected_total)):
        raise ValueError("diagnostic selection changed during pagination")
    rows = sanitized_rows(payload, include_detail=bool(reason_code))
    if len(rows) != len(items) or len(selectable_event_ids(rows)) != len(rows):
        raise ValueError("diagnostic selection contains ambiguous event IDs")
    # Since Core #2569, its module filter may match an attested business_module
    # while the physical app_log logger remains "cifereca". The general-purpose
    # public sanitizer intentionally drops that optional metadata, so retain
    # only a fixed Core-published enum internally for the bulk scope check.
    # Never trust or export arbitrary business_module strings, and never let
    # one turn a core_events row or an unknown source into a matching log.
    by_id = {
        item.get("event_id"): item for item in items
        if isinstance(item, Mapping) and type(item.get("event_id")) is str
    }
    for row in rows:
        raw = by_id.get(row["event_id"])
        if (row["source"] == "app_log" and isinstance(raw, Mapping)
                and raw.get("source") == "app_log"
                and raw.get("module") == row["module"]
                and type(raw.get("business_module")) is str
                and raw["business_module"] in _ATTESTED_LOG_DOMAINS):
            row["business_module"] = raw["business_module"]
    following = next_page_offset(payload, current_offset=offset)
    if following is None:
        if offset + len(items) != total:
            raise ValueError("diagnostic selection is incomplete")
    elif following != offset + len(items):
        raise ValueError("diagnostic selection has missing rows")
    return rows, total, following


def collect_filtered_selection(
    broker, *, source="journal", module="", severity="", period="day", search="",
    provider_id="", reason_code="", domain="", mode="errors",
    cancelled: Callable[[], bool] | None = None,
    max_pages=MAX_SCAN_PAGES,
) -> FilteredSelection:
    """Scan available filtered Core events in background, or refuse atomically.

    Error selection queries the two *server-side* severity feeds regardless
    of the current severity dropdown. It still applies source, module, period
    and the local text search to each page. "Tout" follows the current
    severity dropdown. 200 events / 96 KB / 8 pages per feed are hard caps;
    an incomplete scan is NEVER returned as a complete selection.
    """
    if mode not in {"errors", "all"}:
        raise ValueError("invalid diagnostic selection mode")
    if (source not in SOURCES or severity not in SEVERITIES
            or period not in PERIODS or type(module) is not str
            or len(module) > 90 or type(search) is not str
            or len(search) > 500
            or type(provider_id) is not str
            or (provider_id and public_provider_id(provider_id) is None)
            or type(reason_code) is not str
            or (reason_code and re.fullmatch(r"[a-z][a-z0-9_]{0,79}", reason_code) is None)
            or type(max_pages) is not int or not 1 <= max_pages <= MAX_SCAN_PAGES):
        raise ValueError("invalid diagnostic selection filters")
    verified_domain = normalize_diagnostic_domain(domain)
    levels = ("error", "critical") if mode == "errors" else (severity,)
    # Capture the relative-period boundary for ALL requests in this scan.
    reference_time = datetime.now(timezone.utc)
    floor = explorer_since(period, now=reference_time)
    period_start = datetime.fromisoformat(floor) if floor is not None else None
    results: list[dict[str, str]] = []
    seen: set[str] = set()
    inspected = 0
    partial = False
    for level in levels:
        offset = 0
        total_expected = None
        first_page_fingerprint = None
        pages = 0
        while True:
            if cancelled is not None and cancelled():
                raise ValueError("diagnostic selection cancelled")
            if pages >= max_pages:
                raise ValueError("diagnostic selection exceeds the safe page limit")
            page = fetch_explorer_page(
                broker, source=source, module=module, severity=level,
                period=period, offset=offset, reference_time=reference_time,
                reason_code=reason_code, provider_id=provider_id,
                domain=verified_domain,
                search=search if source == "journal" else "",
            )
            rows, total, following = _checked_page(
                page, offset, total_expected, reason_code=reason_code,
            )
            _check_selected_scope(rows, source=source, module=module,
                                  severity=level, since=period_start,
                                  reason_code=reason_code, provider_id=provider_id,
                                  domain=verified_domain)
            total_expected = total
            if first_page_fingerprint is None:
                first_page_fingerprint = _redacted_page_fingerprint(rows)
            pages += 1
            inspected += len(rows)
            statuses = source_summaries(page)
            if not statuses or any(s["status"] not in {"available", "empty"} for s in statuses):
                partial = True
            for row in rows:
                event_id = row["event_id"]
                if event_id in seen:
                    raise ValueError("diagnostic selection contains duplicate event IDs")
                seen.add(event_id)
                if search and source != "journal" and search.casefold() not in " ".join(
                    row[key] for key in
                    ("timestamp", "source", "module", "severity", "message", "status")
                ).casefold():
                    continue
                results.append(row)
                if len(results) > MAX_SELECTED:
                    raise ValueError("diagnostic selection exceeds 200 events")
            if following is None:
                break
            offset = following

        # Offset pagination is not atomic in Core. Recheck the leading page to
        # detect rotation/reordering instead of silently exporting a mixed feed.
        if cancelled is not None and cancelled():
            raise ValueError("diagnostic selection cancelled")
        current = fetch_explorer_page(
            broker, source=source, module=module, severity=level,
            period=period, offset=0, reference_time=reference_time,
            reason_code=reason_code, provider_id=provider_id,
            domain=verified_domain,
            search=search if source == "journal" else "",
        )
        reread, _total, _next = _checked_page(
            current, 0, total_expected, reason_code=reason_code,
        )
        _check_selected_scope(reread, source=source, module=module,
                              severity=level, since=period_start,
                              reason_code=reason_code, provider_id=provider_id,
                              domain=verified_domain)
        if _redacted_page_fingerprint(reread) != first_page_fingerprint:
            raise ValueError("diagnostic selection changed during pagination")

    event_ids = frozenset(row["event_id"] for row in results)
    # The background-only bulk path validates *all* selected rows using
    # the same field allowlist and second redaction as the 50-row page export.
    # No partial/truncated clipboard payload is returned on any overflow.
    try:
        packed = json.loads(export_selected_events(
            results, set(event_ids),
            max_events=MAX_SELECTED, max_bytes=MAX_EXPORT_BYTES,
        ))
    except ValueError as exc:
        if str(exc) == "diagnostic export exceeds the 96 KB limit":
            raise ValueError("diagnostic selection exceeds 96 KB") from None
        raise
    packed["selection"] = {
        "scope": "all_accessible_filtered_pages",
        "event_count": len(results),
        "inspected_count": inspected,
        "source_filter": source,
        "severity_filter": "error_and_critical" if mode == "errors" else (severity or "all"),
        "period": period,
        "reconciliation_reason_code": reason_code or None,
        "provider_filter": provider_id or None,
        "domain_filter": verified_domain or None,
        "local_text_search_applied": bool(search),
        "pagination_consistency": "first_page_rechecked_non_atomic",
        "sources_partial_or_unavailable": partial,
        "details": "Published redacted summaries; 500 characters maximum per field",
    }
    result = json.dumps(packed, ensure_ascii=False, indent=2, allow_nan=False)
    try:
        if len(result.encode("utf-8")) > MAX_EXPORT_BYTES:
            raise ValueError("diagnostic selection exceeds 96 KB")
    except UnicodeError:
        raise ValueError("diagnostic selection contains invalid Unicode") from None
    return FilteredSelection(event_ids, result, len(results), inspected, partial)
