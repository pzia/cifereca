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

from .diagnostic_log_line import _LOG_LINE

_MAX_FILE_BYTES = 2_000_000
_MAX_EVENT_BYTES = 256_000
_ROTATIONS = (3, 2, 1, 0)
_LEVELS = frozenset({"debug", "info", "warning", "error", "critical"})
_DIAGNOSTIC_PREFIX = "DIAG_JSON "
# Exactly the existing Core worker terminal emission. A free-text mention,
# another logger or a severity/status contradiction is NOT a task reference.
_TASK_CORRELATION = re.compile(
    r"TRACE task correlation task_ref=([0-9a-f]{12}) "
    r"status=(success|failed|cancelled)", re.ASCII,
)
_TASK_SEVERITY = {"success": "info", "failed": "error", "cancelled": "warning"}


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
    duration_ms = None
    structured_unreadable = False
    if message.startswith(_DIAGNOSTIC_PREFIX):
        try:
            document = json.loads(message[len(_DIAGNOSTIC_PREFIX):])
        except (ValueError, TypeError, RecursionError):
            document = None
        # A broken or unknown DIAG_JSON envelope is still an observed log
        # occurrence. Keep its RAW text, but do not silently call the
        # structured history complete: operation, status, task and provider
        # evidence cannot be recovered from this record.
        if (type(document) is not dict
                or type(document.get("schema_version")) is not int
                or document["schema_version"] != 1):
            structured_unreadable = True
        elif (any(
            key in document and type(document[key]) is not kind
            for key, kind in (
                ("message", str), ("category", str), ("operation", str),
                ("status", str), ("detail", dict), ("task_ref", str),
            )
        ) or (
            "duration_ms" in document and (
                type(document["duration_ms"]) is not int
                or not 0 <= document["duration_ms"] <= 86_400_000
            )
        )):
            # A schema-1 field present with the WRONG TYPE is not evidence
            # of a missing operation/status/owner. Never silently replace it
            # with "unknown", empty detail or an invented default while
            # claiming a complete history. Preserve the raw DIAG_JSON text.
            structured_unreadable = True
        else:
            message = document.get("message") if type(document.get("message")) is str else message
            category = document.get("category") if type(document.get("category")) is str else category
            operation = document.get("operation") if type(document.get("operation")) is str else ""
            status = document.get("status") if type(document.get("status")) is str else "unknown"
            detail = document.get("detail") if type(document.get("detail")) is dict else {}
            task_ref = document.get("task_ref") if type(document.get("task_ref")) is str else None
            value_ms = document.get("duration_ms")
            if type(value_ms) is int and 0 <= value_ms <= 86_400_000:
                duration_ms = value_ms
    elif match["logger"] == "cifereca":
        # The unstructured legacy worker line has an exact, finite Core
        # format. Preserve its already emitted task_ref and outcome on the
        # canonical journal surface; never infer a provider, cause or GET.
        correlation = _TASK_CORRELATION.fullmatch(message)
        if (correlation is not None
                and level == _TASK_SEVERITY[correlation.group(2)]):
            task_ref, status = correlation.groups()
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
    # Exact structured field, never inferred from an event message or logger.
    # The UI provider filter and explicit source-scope checks need this same
    # value at the top level; the full original is retained in detail.
    provider_id = detail.get("provider_id")
    if type(provider_id) is str and 0 < len(provider_id) <= 90:
        event["provider_id"] = provider_id
    elif (match["logger"] == "cifereca"
          and category == "ACQUISITION" and operation == "loan sync"
          and status in {"warning", "error"}
          and type(detail) is dict):
        # Historical Core opening-loan diagnostics (#2888) emitted the
        # *observed source provider* under 'provider', not 'provider_id'.
        # Restrict this compatibility projection to the precise Core message
        # shape and the two supported loan sources. Never infer a provider
        # from a free-text message, an ADEPT runtime, or an account label.
        legacy_provider = detail.get("provider")
        expected_outcome = (
            "attention_required" if status == "warning" else "retryable_error"
        )
        if (type(legacy_provider) is str
                and legacy_provider in {"paris", "bnf-pret"}
                and detail.get("status") == expected_outcome
                and type(detail.get("code")) is str
                and 0 < len(detail["code"]) <= 90
                and type(detail.get("loan_id")) is str
                and 0 < len(detail["loan_id"]) <= 4096):
            event["provider_id"] = legacy_provider
            event["provider_id_origin"] = "legacy_opening_loan_sync"
    elif (match["logger"] == "cifereca"
          and category == "NUMILOG"
          and operation == "startup_materialization"):
        # The validated Core Numilog startup diagnostic is provider-scoped
        # even though its closed schema intentionally carries no provider_id.
        # It is a *preflight/materialization observation*, not a confirmed
        # download or right. An arbitrary NUMILOG message or plugin line
        # cannot acquire this association.
        from .numilog_startup_diagnostics import public_fields as numilog_fields
        checked = numilog_fields(detail)
        expected = {
            "failed": ("error", "error"),
            "partial": ("error", "warning"),
            "blocked": ("warning", "warning"),
            "complete": ("ok", "info"),
            "not_due": ("ok", "info"),
            "running": ("ok", "info"),
        }
        if (checked and checked == detail
                and (status, level) == expected.get(checked["status"])):
            event["provider_id"] = "numilog"
            event["provider_id_origin"] = "core_numilog_startup_observation"
    if task_ref:
        event["task_ref"] = task_ref
    if duration_ms is not None:
        event["duration_ms"] = duration_ms
    return event, structured_unreadable


def _events(log_path: Path):
    events = []
    gaps = []
    active_missing = False
    present_rotations = set()
    missing_rotations = set()
    for rotation in _ROTATIONS:
        path = Path(f"{log_path}.{rotation}" if rotation else str(log_path))
        lines, state, identity = _lines(path)
        if state == "missing":
            if rotation == 0:
                active_missing = True
            else:
                missing_rotations.add(rotation)
            continue
        # A missing *oldest* backup is normal before enough rotations exist.
        # A hole BETWEEN two present log files is different: some events
        # have disappeared, even if every remaining file parses correctly.
        present_rotations.add(rotation)
        if state != "available":
            gaps.append(f"rotation_{rotation}_{state}")
            continue
        current = None  # Never attach a new file's orphan to the prior rotation.
        for offset, line in lines:
            m = _LOG_LINE.match(line)
            if m:
                current = None
                try:
                    current, structured_unreadable = _make_event(m, identity, offset)
                    events.append(current)
                    if structured_unreadable and "structured_event_unreadable" not in gaps:
                        gaps.append("structured_event_unreadable")
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
    for rotation in sorted(missing_rotations, reverse=True):
        if any(older > rotation for older in present_rotations) and any(
            newer < rotation for newer in present_rotations
        ):
            gaps.append(f"rotation_{rotation}_missing")
    return events, gaps, active_missing


def query_journal(
    log_path: Path, *, search: str = "", module: str = "",
    severity: str = "", provider_id: str = "",
    since: str | None = None, until: str | None = None,
    limit: int = 50, offset: int = 0, sort_order: str = "asc",
) -> dict[str, Any]:
    """Search all retained rotations and apply global order BEFORE pagination."""
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
    if type(sort_order) is not str or sort_order not in {"asc", "desc"}:
        raise ValueError("invalid journal sort order")

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
            # Exact Core-structured provider_id, or the specifically scoped
            # legacy opening-loan observation projected by _make_event.
            # Neither a free-text mention nor a logger/module name qualifies.
            if event.get("provider_id") != provider_id:
                continue
        instant = datetime.fromisoformat(event["timestamp"])
        if lower and instant < lower or upper and instant > upper:
            continue
        if needle:
            # Search original observed log/structured fields, not the journal
            # reader's synthetic envelope. Otherwise every query for
            # "journal" or "schema_version" matches ALL events solely
            # because these labels were added during projection.
            # JSON object keys in this reader's envelope (e.g. "module",
            # "severity", "status", "message", "logger") did not occur as
            # words in an unstructured log line. Searching a serialized event
            # object makes every such query spuriously match every row.
            #
            # Search observed VALUES only. Preserve source-provided detail
            # dictionaries with their real keys, including phase/reason_code.
            observed = [
                event["timestamp"], event["logger"], event["severity"],
                event["module"], event["message"],
            ]
            observed.extend(event[key] for key in (
                "operation", "task_ref", "duration_ms",
            ) if key in event)
            if event.get("detail"):
                observed.append(event["detail"])
            if (event.get("operation") or event.get("detail")
                    or event["status"] != "unknown"):
                observed.append(event["status"])
            if needle not in _fold(json.dumps(
                observed, ensure_ascii=False, default=str,
            )):
                continue
        matched.append(event)
    # Python's stable sort preserves physical log order for timestamps with
    # the same millisecond. Sorting ties by the SHA-derived event_id scrambles
    # the actual emission order (including across rotated files).
    # The GUI must page the newest retained records GLOBALLY, not reverse
    # only the oldest 50 returned by an ascending Core query.
    matched.sort(key=lambda row: row["timestamp"])
    if sort_order == "desc":
        matched.reverse()  # Equal timestamps: latest physical occurrence first.
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



def recent_diagnostics_snapshot(log_path: Path, *, limit: int = 100) -> tuple[dict[str, Any], ...]:
    """Compatibility snapshot sourced solely from persisted structured events.

    The snapshot is not another collector: each row retains its physical
    journal event ID and unchanged diagnostic payload. Plain application logs
    belong to events(), not the historical structured snapshot surface.
    """
    if type(limit) is not int or not 1 <= limit <= 500:
        raise ValueError("invalid journal snapshot limit")
    events, _gaps, _missing = _events(Path(log_path))
    structured = [row for row in events if row.get("operation")]
    # Keep the same physical tie order as query_journal and raw export.
    structured.sort(key=lambda row: row["timestamp"])
    result = []
    for event in structured[-limit:]:
        row = {
            "event_id": event["event_id"],
            "timestamp": event["timestamp"],
            "source": "journal",
            "category": event["module"],
            "operation": event["operation"],
            "status": event["status"],
            "severity": event["severity"],
            "message": event["message"],
            "detail": dict(event.get("detail") or {}),
        }
        if "duration_ms" in event:
            row["duration_ms"] = event["duration_ms"]
        if "task_ref" in event:
            row["task_ref"] = event["task_ref"]
        result.append(row)
    return tuple(result)


def journal_text_export(log_path: Path, *, version: str) -> str:
    """Human-openable NDJSON export of the SAME physical events as raw export.

    No masking is performed. Exports remain explicitly local and bounded by
    the raw journal's size and coverage limits.
    """
    try:
        document = json.loads(export_raw_journal(log_path))
    except ValueError as exc:
        if str(exc) != "journal_empty":
            raise
        document = {
            "coverage": "empty_or_missing", "rotation_gaps": [],
            "active_log_missing": _lines(Path(log_path))[1] == "missing", "events": [],
        }
    header = [
        f"cifereca v{version}",
        "format: cifereca-journal-ndjson",
        "coverage: " + str(document["coverage"]),
        "active_log_missing: " + str(document["active_log_missing"]).lower(),
        "rotation_gaps: " + json.dumps(document["rotation_gaps"], ensure_ascii=False),
    ]
    return "\n".join([
        *header,
        *(json.dumps(event, ensure_ascii=False, allow_nan=False)
          for event in document["events"]),
        "",
    ])


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
