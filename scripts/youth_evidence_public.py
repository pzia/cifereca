"""Strict public schema for manual Youth HTTPS evidence (#1144).

Only this sanitized representation may reach a file, Actions summary or artifact.
The qualification engine can retain detailed in-memory data for evaluation.
"""
from __future__ import annotations

import re
from collections.abc import Mapping

_ALLOWED_RESULTS = frozenset({"PASS", "BLOCKED", "NO_VERIFIED_NOTICE", "NOT_RUN", "INVALID_INPUT"})
_ERROR_CODES = {
    "TimeoutException": "NETWORK_TIMEOUT", "ConnectTimeout": "NETWORK_TIMEOUT",
    "ReadTimeout": "NETWORK_TIMEOUT", "PoolTimeout": "NETWORK_TIMEOUT",
    "ConnectError": "NETWORK_CONNECTION", "HTTPError": "HTTP_ERROR",
    "HTTPStatusError": "HTTP_STATUS", "RuntimeError": "QUALIFICATION_RUNTIME",
    "ValueError": "INVALID_SOURCE_DATA", "TypeError": "INVALID_SOURCE_DATA",
    "KeyError": "INVALID_SOURCE_DATA", "JSONDecodeError": "INVALID_SOURCE_DATA",
    "FileNotFoundError": "LOCAL_SETUP_ERROR", "PermissionError": "LOCAL_SETUP_ERROR",
}
_DIAGNOSTIC_INTS = frozenset({
    "page", "requested_limit", "detail_requests", "http_status", "html_bytes",
    "anchor_count", "milan_catalogue_href_count", "product_href_count",
    "product_link_text_count", "product_link_accessible_count",
    "product_link_unnamed_count", "unrecognized_catalogue_href_count",
    "recognized_product_count", "qualified_items", "request_count",
    "product_url_count", "returned_items", "qualified_age_count",
    "unqualified_age_count", "duration_ms",
})
_DIAGNOSTIC_FLAGS = frozenset({
    "age_filter_present", "content_type_is_html", "next_cursor_present",
    "text_filter_present",
})
_PHASES = frozenset({"listing", "products", "parsing", "complete"})
_STATES = frozenset({"items", "empty_unverified", "error"})


def _bounded_int(value: object, upper: int = 100_000_000) -> int | None:
    return value if type(value) is int and 0 <= value <= upper else None


def _diagnostic(value: object, source: str) -> dict:
    if not isinstance(value, Mapping) or value.get("source") != source:
        return {}
    safe = {"source": source}
    for key in _DIAGNOSTIC_INTS:
        item = _bounded_int(value.get(key))
        if item is not None and (key != "http_status" or 100 <= item <= 599):
            safe[key] = item
    for key in _DIAGNOSTIC_FLAGS:
        if type(value.get(key)) is bool:
            safe[key] = value[key]
    phase = value.get("phase")
    if isinstance(phase, str) and phase in _PHASES:
        safe["phase"] = phase
    state = value.get("state")
    if isinstance(state, str) and state in _STATES:
        safe["state"] = state
    digest = value.get("html_sha256_prefix")
    if isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{16}", digest):
        safe["html_sha256_prefix"] = digest
    if isinstance(value.get("error_type"), str):
        safe["error_code"] = _ERROR_CODES.get(value["error_type"], "OTHER_ERROR")
    return safe


def public_report(report: Mapping[str, object], sources: Mapping[str, Mapping]) -> dict:
    """Fail-closed export: never copy plugin strings or arbitrary URL/notice data.

    No source-provided title, ISBN, search string, request URL, path, error,
    age label, header, cookie, account, or user profile can reach this schema.
    """
    source = report.get("source")
    if not isinstance(source, str) or source not in sources:
        return {"result": "BLOCKED", "reason_code": "INVALID_SOURCE"}
    declared = sources[source]
    result = report.get("result")
    if not isinstance(result, str) or result not in _ALLOWED_RESULTS:
        result = "BLOCKED"
    safe: dict[str, object] = {
        "schema": "youth-public-evidence/1",
        "source": source,
        "allowed_host": declared["host"],
        "source_plugin_id": declared["plugin"],
        "result": result,
    }
    for key, cap in (("age", 17), ("limit", 3), ("request_budget", 20),
                     ("requests_count", 20), ("accepted", 100_000_000),
                     ("rejected", 100_000_000)):
        value = _bounded_int(report.get(key), cap)
        if value is not None:
            safe[key] = value
    notices = report.get("qualified_notices")
    if isinstance(notices, (tuple, list)):
        safe["qualified_notices_count"] = min(len(notices), 100_000_000)
    errors = report.get("ingest_errors")
    if isinstance(errors, (tuple, list)):
        safe["ingest_error_count"] = min(len(errors), 100_000_000)
    if type(report.get("next_cursor_present")) is bool:
        safe["next_cursor_present"] = report["next_cursor_present"]
    version = report.get("core_version")
    if isinstance(version, str) and re.fullmatch(r"[0-9]+(?:\.[0-9]+){1,3}(?:[abrc][0-9]+)?", version):
        safe["core_version"] = version
    commit = report.get("source_commit")
    if isinstance(commit, str) and re.fullmatch(r"[a-fA-F0-9]{40}", commit):
        safe["source_commit"] = commit.lower()
    diag = _diagnostic(report.get("plugin_diagnostic"), source)
    if diag:
        safe["plugin_diagnostic"] = diag
    if result == "NOT_RUN":
        safe["reason_code"] = "CONFIRM_LIVE_REQUIRED"
    elif result == "INVALID_INPUT":
        safe["reason_code"] = "INVALID_INPUT"
    elif result == "BLOCKED":
        if safe.get("requests_count") == 0:
            safe["reason_code"] = "NO_HTTPS_REQUEST"
        else:
            safe["reason_code"] = "QUALIFICATION_BLOCKED"
    elif result == "NO_VERIFIED_NOTICE":
        safe["reason_code"] = "NO_VERIFIED_NOTICE"
    error_type = report.get("error_type")
    if isinstance(error_type, str):
        safe["error_code"] = _ERROR_CODES.get(error_type, "OTHER_ERROR")
    return safe
