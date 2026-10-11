"""Bounded, read-only public Diagnostics event feed.

Never expose raw application logs, filesystem paths, credentials or arbitrary
runtime output through the plugin broker. Local log files are only opened from
the Core-owned fixed log location. There is no network or subprocess access.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import os
from pathlib import Path
import re
import stat
from typing import Any

_LOG_LINE = re.compile(
    r"^(?P<date>\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d+) "
    r"(?P<level>DEBUG|INFO|WARNING|ERROR|CRITICAL) "
    r"(?P<logger>[\w.-]{1,90}) (?:\[[^\]]{0,80}\] )?(?P<body>.*)$"
)
_TRACE = re.compile(r"^TRACE ([a-zA-Z][\w-]{0,30}) ([a-zA-Z][\w-]{0,45})(?:\s|$)")
_SAFE_FIELDS = re.compile(
    r"\b(phase|status|outcome|operation|duration_ms|count|returncode)="
    r"([a-zA-Z][a-zA-Z_-]{0,32}|\d{1,6})\b"
)
_ALLOWED_FIELDS = {"phase", "status", "outcome", "operation", "duration_ms", "count", "returncode"}
_MAX_BYTES_PER_LOG = 32768
_MAX_EVENTS_PER_FILE = 200
_MAX_RESULTS = 100
_STRUCTURED_TERMS = frozenset({
    "sync", "source", "opening", "connect", "download", "acquire", "acquisition",
    "return", "verification", "retry", "import", "runtime", "plugin", "parse",
    "catalog", "search", "library", "update", "install", "check", "health",
    "error", "warning", "state", "fetch", "adept", "pnb", "paris", "kobo",
})
_CATEGORIES = frozenset({
    "CORE", "SYNC", "ADEPT", "PNB", "PARIS", "KOBO", "PLUGIN",
    "UPDATER", "UI", "CATALOG", "DIAGNOSTICS", "IMPORT", "LIBRARY",
})


def _stamp(value: str) -> datetime:
    instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if instant.tzinfo is None:
        raise ValueError("timezone required for diagnostic date filters")
    return instant.astimezone(timezone.utc)


def _event_id(source: str, timestamp: str, detail: str) -> str:
    return sha256((source + "\0" + timestamp + "\0" + detail).encode()).hexdigest()[:16]


def _log_lines(path: Path) -> tuple[list[str], str]:
    """Read at most 32 KiB from an existing regular, non-symlink file."""
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags)
        with os.fdopen(fd, "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                return [], "unavailable"
            length = os.fstat(handle.fileno()).st_size
            start = max(0, length - _MAX_BYTES_PER_LOG)
            handle.seek(start)
            data = handle.read(_MAX_BYTES_PER_LOG)
            text = data.decode("utf-8", errors="replace").splitlines()
            if start and text:
                text.pop(0)  # first line may be a partial record
            return text[-_MAX_EVENTS_PER_FILE:], "partial" if start else "available"
    except (OSError, ValueError):
        return [], "unavailable"


def _source_logs(log_path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    events: list[dict[str, Any]] = []
    observed = False
    partial = False
    for index in (3, 2, 1, 0):
        path = Path(str(log_path) + (f".{index}" if index else ""))
        lines, status = _log_lines(path)
        if status == "unavailable":
            continue
        observed = True
        partial |= status == "partial"
        for line_number, line in enumerate(lines):
            match = _LOG_LINE.match(line)
            if not match or not match["logger"].startswith("cifereca"):
                continue
            # Raw log messages may contain arbitrary PII or ACSM/licence URLs.
            # Export only known machine-generated TRACE categories and small
            # allowlisted status fields. Everything else remains private.
            trace = _TRACE.match(match["body"])
            if trace:
                message = "TRACE " + trace.group(1) + " " + trace.group(2)
                suffix = match["body"][trace.end():]
                pairs = _SAFE_FIELDS.findall(suffix)[:4]
                if pairs:
                    message += " " + " ".join(k + "=" + v for k, v in pairs if k in _ALLOWED_FIELDS)
            else:
                message = "[contenu libre masqué : consulter le journal local]"
            try:
                ts = datetime.strptime(match["date"], "%Y-%m-%d %H:%M:%S,%f")
                ts = ts.astimezone().isoformat()
            except ValueError:
                continue
            events.append({
                "timestamp": ts,
                "source": "app_log",
                "module": match["logger"][:90],
                "severity": match["level"].lower(),
                "message": message[:240],
                "event_id": _event_id("app_log", ts, f"{index}:{line_number}:{message}"),
            })
    status = "partial" if partial else ("available" if observed else "unavailable")
    return events, {"source": "app_log", "status": "partial" if partial else (status if events else ("empty" if observed else "unavailable")),
                    "reason": "log_tail_bounded" if partial else ("no_matching_events" if observed and not events else "")}


def _source_diagnostics(diagnostics: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not getattr(diagnostics, "enabled", True):
        return [], {"source": "core_events", "status": "disabled", "reason": "diagnostics_disabled"}
    try:
        rows = diagnostics.snapshot()[-500:]
    except (AttributeError, ValueError, TypeError):
        return [], {"source": "core_events", "status": "unavailable", "reason": "snapshot_unavailable"}
    events: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        stamp = str(row.get("timestamp") or "")
        try:
            _stamp(stamp)
        except (ValueError, TypeError):
            continue
        category = str(row.get("category") or "").upper()
        module = category if category in _CATEGORIES else "DIAGNOSTICS"
        operation = str(row.get("operation") or "")
        status = str(row.get("status") or "").lower()
        severity = "error" if status in {"error", "failed"} else "warning" if status in {
            "warning", "attention_required", "partial"
        } else "info"
        # Even structured Diagnostics text can include names/paths. Publish
        # only a closed vocabulary of operational terms, never raw free text.
        terms = [word for word in re.findall(r"[a-zA-Z]+", operation.lower())
                 if word in _STRUCTURED_TERMS][:5]
        message = " ".join(terms) if terms else "[opération masquée]"
        events.append({
            "timestamp": stamp,
            "source": "core_events",
            "module": module,
            "severity": severity,
            "message": message,
            "status": status if status in {"ok", "error", "failed", "warning",
                                         "attention_required", "partial", "success"}
                       else "unknown",
            "event_id": _event_id("core_events", stamp, category + ":" + operation),
        })
    return events, {"source": "core_events", "status": "available" if events else "empty",
                    "reason": "" if events else "no_recorded_events"}


def query_diagnostic_events(
    diagnostics: Any, *, log_path: Path,
    source: str = "all", module: str = "", severity: str = "",
    since: str | None = None, until: str | None = None,
    limit: int = 50, offset: int = 0,
) -> dict[str, Any]:
    """A bounded/paginated snapshot with explicit source states.

    Log content is intentionally *not* an unredacted viewer. The caller may
    only select sources and filters, never a filesystem path.
    """
    if source not in {"all", "core_events", "app_log"}:
        raise ValueError("unknown diagnostic source")
    if not isinstance(module, str) or len(module) > 90:
        raise ValueError("diagnostic module must be a string of at most 90 characters")
    if not isinstance(severity, str) or severity.lower() not in {"", "debug", "info", "warning", "error", "critical"}:
        raise ValueError("unknown diagnostic severity")
    if any(value is not None and not isinstance(value, str) for value in (since, until)):
        raise ValueError("diagnostic date filters must be ISO-8601 strings")
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= _MAX_RESULTS:
        raise ValueError("diagnostic limit must be 1..100")
    if not isinstance(offset, int) or isinstance(offset, bool) or not 0 <= offset <= 10000:
        raise ValueError("invalid diagnostic offset")
    lower = _stamp(since) if since is not None else None
    upper = _stamp(until) if until is not None else None
    if lower and upper and lower > upper:
        raise ValueError("diagnostic date range reversed")
    events: list[dict[str, Any]] = []
    states: list[dict[str, Any]] = []
    if source in {"all", "core_events"}:
        rows, state = _source_diagnostics(diagnostics)
        events.extend(rows)
        states.append(state)
    if source in {"all", "app_log"}:
        rows, state = _source_logs(log_path)
        events.extend(rows)
        states.append(state)
    if module:
        events = [row for row in events if row["module"].casefold().startswith(module.casefold()[:90])]
    if severity:
        if severity.lower() not in {"debug", "info", "warning", "error", "critical"}:
            raise ValueError("unknown diagnostic severity")
        events = [row for row in events if row["severity"] == severity.lower()]
    if lower or upper:
        events = [
            row for row in events
            if (not lower or _stamp(row["timestamp"]) >= lower)
            and (not upper or _stamp(row["timestamp"]) <= upper)
        ]
    events.sort(key=lambda row: (_stamp(row["timestamp"]), row["source"], row["event_id"]))
    total = len(events)
    page = events[offset:offset + limit]
    return {
        "status": "partial" if any(st["status"] == "partial" for st in states) else (
            "available" if total else "empty"
        ),
        "sources": states,
        "events": page,
        "total": total,
        "limit": limit,
        "offset": offset,
        "next_offset": offset + len(page) if offset + len(page) < total else None,
        "read_only": True,
        "bounded": True,
    }
