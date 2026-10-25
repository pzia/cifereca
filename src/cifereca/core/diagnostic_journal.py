"""Local canonical event journal over the one rotating Cifereca log (#3133).

Raw, read-only and bounded by physical log retention. It neither scrubs nor
activates providers. Historical memory/core_events queries remain a temporary
compatibility facade; new consumers request source='journal'.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat
import unicodedata
from typing import Any

from .diagnostic_event_feed import _LOG_LINE

_MAX_FILE_BYTES = 2_000_000
_MAX_EVENT_BYTES = 256_000
_ROTATIONS = (3, 2, 1, 0)
_LEVELS = frozenset({"debug", "info", "warning", "error", "critical"})
_DIAGNOSTIC_PREFIX = "DIAG_JSON "


def journal_payload(value: Any, depth: int = 0) -> Any:
    """Retain raw JSON-compatible values; never execute hostile object hooks."""
    if depth > 12:
        return None
    if type(value) in (str, int, bool) or value is None:
        return value
    if type(value) is float:
        import math
        return value if math.isfinite(value) else None
    if type(value) is dict:
        return {
            key: journal_payload(item, depth + 1)
            for key, item in value.items()
            if type(key) is str
        }
    if type(value) in (list, tuple):
        return [journal_payload(item, depth + 1) for item in value]
    return None


def _fold(value: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", value.casefold())
                   if not unicodedata.combining(c))


def _timestamp(value: str) -> str:
    # Standard logging.Formatter date uses the host's local timezone.
    # Preserve real instants when presenting events to clients in UTC.
    instant = datetime.strptime(value, "%Y-%m-%d %H:%M:%S,%f")
    return instant.astimezone().astimezone(timezone.utc).isoformat(timespec="milliseconds")


def _lines(path: Path):
    """Read one trusted regular file without traversing a symlinked directory."""
    if not all(hasattr(os, name) for name in ("O_NOFOLLOW", "O_DIRECTORY")):
        return [], "unavailable", None
    directory_fd = -1
    try:
        absolute = Path(os.path.abspath(path))
        directory_fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        for part in absolute.parts[1:-1]:
            following = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = following
        fd = os.open(absolute.name, os.O_RDONLY | os.O_NOFOLLOW,
                     dir_fd=directory_fd)
        with os.fdopen(fd, "rb") as stream:
            file_stat = os.fstat(stream.fileno())
            if not stat.S_ISREG(file_stat.st_mode):
                return [], "unavailable", None
            if file_stat.st_size > _MAX_FILE_BYTES:
                return [], "partial", (file_stat.st_dev, file_stat.st_ino)
            payload = stream.read(_MAX_FILE_BYTES + 1)
            if len(payload) > _MAX_FILE_BYTES:
                return [], "partial", (file_stat.st_dev, file_stat.st_ino)
            lines = []
            offset = 0
            for raw in payload.splitlines(keepends=True):
                lines.append((offset, raw.decode("utf-8", errors="replace").rstrip("\r\n")))
                offset += len(raw)
            return lines, "available", (file_stat.st_dev, file_stat.st_ino)
    except FileNotFoundError:
        return [], "missing", None
    except (OSError, ValueError):
        return [], "unavailable", None
    finally:
        if directory_fd >= 0:
            os.close(directory_fd)


def _make_event(match, identity, offset):
    level = match["level"].lower()
    message = match["body"]
    detail = {}
    category = match["logger"]
    operation = ""
    status = "unknown"
    task_ref = None
    if message.startswith(_DIAGNOSTIC_PREFIX):
        try:
            document = json.loads(message[len(_DIAGNOSTIC_PREFIX):])
        except (ValueError, TypeError):
            document = None
        if type(document) is dict and document.get("schema_version") == 1:
            message = document.get("message") if type(document.get("message")) is str else message
            category = document.get("category") if type(document.get("category")) is str else category
            operation = document.get("operation") if type(document.get("operation")) is str else ""
            status = document.get("status") if type(document.get("status")) is str else "unknown"
            detail = document.get("detail") if type(document.get("detail")) is dict else {}
            task_ref = document.get("task_ref") if type(document.get("task_ref")) is str else None
    # A physical occurrence is identified by inode + offset, not a timestamp
    # or textual hash. Identical messages and rotation renames remain distinct.
    id_bytes = f"{identity[0]}:{identity[1]}:{offset}".encode("ascii")
    event = {
        "event_id": sha256(id_bytes).hexdigest()[:20],
        "schema_version": 1,
        "timestamp": _timestamp(match["date"]),
        "source": "journal",
        "module": category,
        "logger": match["logger"],
        "severity": level,
        "message": message,
        "status": status,
    }
    if operation:
        event["operation"] = operation
    if detail:
        event["detail"] = detail
    if task_ref:
        event["task_ref"] = task_ref
    return event


def _events(log_path: Path):
    events = []
    gaps = []
    active_missing = False
    for rotation in _ROTATIONS:
        path = Path(f"{log_path}.{rotation}" if rotation else str(log_path))
        lines, state, identity = _lines(path)
        if state == "missing":
            if rotation == 0:
                active_missing = True
            continue
        if state != "available":
            gaps.append(f"rotation_{rotation}_{state}")
            continue
        current = None  # Never attach a new file's orphan to the prior rotation.
        for offset, line in lines:
            m = _LOG_LINE.match(line)
            if m:
                current = None
                try:
                    current = _make_event(m, identity, offset)
                    events.append(current)
                except (ValueError, OverflowError):
                    gaps.append("timestamp_invalid")
            elif line:
                # Tracebacks can continue across lines, but a rotation cannot
                # attest that its orphan prefix belongs to the previous inode.
                if current is None:
                    if "orphan_log_line" not in gaps:
                        gaps.append("orphan_log_line")
                    continue
                if len(current["message"]) + len(line) + 1 <= _MAX_EVENT_BYTES:
                    current["message"] += "\n" + line
                elif "event_text_exceeded" not in gaps:
                    gaps.append("event_text_exceeded")
    return events, gaps, active_missing


def query_journal(
    log_path: Path, *, search: str = "", module: str = "",
    severity: str = "", provider_id: str = "",
    since: str | None = None, until: str | None = None,
    limit: int = 50, offset: int = 0,
) -> dict[str, Any]:
    """Search all retained rotations BEFORE pagination, with honest coverage."""
    if type(search) is not str or len(search) > 500:
        raise ValueError("invalid journal search")
    if type(module) is not str or len(module) > 90:
        raise ValueError("invalid journal module")
    if type(provider_id) is not str or len(provider_id) > 90:
        raise ValueError("invalid journal provider")
    if type(severity) is not str or severity not in (_LEVELS | {""}):
        raise ValueError("invalid journal severity")
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("invalid journal limit")
    if type(offset) is not int or not 0 <= offset <= 1000000:
        raise ValueError("invalid journal offset")

    def bound(value):
        if value is None:
            return None
        if type(value) is not str or len(value) > 64:
            raise ValueError("invalid journal date")
        instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if instant.tzinfo is None:
            raise ValueError("journal dates must include timezone")
        return instant.astimezone(timezone.utc)

    lower, upper = bound(since), bound(until)
    if lower and upper and lower > upper:
        raise ValueError("invalid journal date range")
    events, gaps, missing = _events(Path(log_path))
    matched = []
    needle = _fold(search)
    for event in events:
        if module and not event["module"].casefold().startswith(module.casefold()):
            continue
        if severity and severity != event["severity"]:
            continue
        if provider_id:
            # Only an actual structured provider_id field, never inference
            # from a message or the module.
            detail = event.get("detail") or {}
            if detail.get("provider_id") != provider_id:
                continue
        instant = datetime.fromisoformat(event["timestamp"])
        if lower and instant < lower or upper and instant > upper:
            continue
        if needle and needle not in _fold(json.dumps(event, ensure_ascii=False, default=str)):
            continue
        matched.append(event)
    matched.sort(key=lambda row: (row["timestamp"], row["event_id"]))
    state = "partial" if gaps or missing else ("available" if events else "empty")
    source_state = {
        "source": "journal", "status": state,
        "reason": "rotation_or_read_gap" if gaps else (
            "active_log_missing" if missing else ("" if events else "no_recorded_events")
        ),
    }
    if events:
        source_state["first_event"] = min(row["timestamp"] for row in events)
        source_state["last_event"] = max(row["timestamp"] for row in events)
    return {
        "status": state,
        "sources": [source_state],
        "total": len(matched),
        "events": matched[offset:offset + limit],
        "limit": limit, "offset": offset,
        "has_more": offset + limit < len(matched),
        "next_offset": offset + limit if offset + limit < len(matched) else None,
    }


def export_raw_journal(log_path: Path) -> bytes:
    """Serialize all retained raw events once, without UI transformations.

    Preserve the journal's coverage limitations and refuse an unexpectedly
    large result; this is an explicit, local action, never called on page load.
    """
    events, gaps, active_missing = _events(Path(log_path))
    if not events and not gaps:
        raise ValueError("journal_empty")
    if len(events) > 50000:
        raise ValueError("journal_export_limit")
    document = {
        "schema_version": 1,
        "format": "cifereca-raw-journal-events",
        "atomic": False,
        "coverage": "partial" if gaps or active_missing else "available",
        "rotation_gaps": gaps,
        "active_log_missing": active_missing,
        "events": events,
    }
    content = json.dumps(document, ensure_ascii=False, allow_nan=False).encode("utf-8")
    if len(content) > 12_000_000:
        raise ValueError("journal_export_limit")
    return content
