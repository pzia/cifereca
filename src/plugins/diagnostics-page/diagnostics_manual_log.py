"""Conservative, offline diagnostics from a user-selected UTF-8 log.

Only fixed technical tokens and counts may cross into a public LLM preview.
No raw line, filename, path, user name, URL, token or hash is published.
Not wired to GTK/LLM yet: selection, consent and invalidation are separate.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import stat

MAX_READ_BYTES = 32768
MAX_LINES = 400
MAX_EVENTS = 12
_SEVERITIES = ("critical", "error", "warning", "info", "debug")
_COMPONENTS = (
    "adept", "acsm", "pnb", "kobo", "sync", "return",
    "borrow", "provider", "plugin", "runtime", "cache",
)
_OPERATIONS = ("timeout", "retry", "failed", "partial", "unavailable", "success")
_TOKEN = re.compile(r"(?<![\w-])(?:" + "|".join(
    _SEVERITIES + ("warn",) + _COMPONENTS + _OPERATIONS
) + r")(?![\w-])", re.IGNORECASE)
_PUBLIC_KEYS = frozenset({
    "schema", "source", "coverage", "sampled_lines", "matched_lines",
    "truncated", "events",
})


def _safe_tokens(line: str) -> tuple[str, str, str]:
    severity = component = operation = ""
    # Fixed-token inspection only. Never retain the match context.
    for match in _TOKEN.finditer(line):
        token = match.group().casefold()
        if token == "warn":
            token = "warning"
        if token in _SEVERITIES and not severity:
            severity = token
        elif token in _COMPONENTS and not component:
            component = token
        elif token in _OPERATIONS and not operation:
            operation = token
        if severity and component and operation:
            break
    return severity, component, operation


def summarize_selected_log_bytes(
    raw: bytes, *, truncated: bool = False,
) -> dict[str, object]:
    """Classify a bounded sample, never emit source text or invented causality."""
    if type(raw) is not bytes or len(raw) > MAX_READ_BYTES or type(truncated) is not bool:
        raise ValueError("selected_log_invalid")
    try:
        text = raw.decode("utf-8")
    except UnicodeError as exc:
        raise ValueError("selected_log_invalid_encoding") from exc
    if "\x00" in text:
        raise ValueError("selected_log_invalid_encoding")
    lines = text.splitlines()
    oversized = len(lines) > MAX_LINES
    lines = lines[-MAX_LINES:]
    events = []
    matched = 0
    for line in lines:
        severity, component, operation = _safe_tokens(line)
        if not (severity or component or operation):
            continue
        matched += 1
        # Only the last events, from the selected sample; these IDs must not
        # be mistaken for original Core event references or absolute lines.
        events.append({
            "severity": severity or "unknown",
            "component": component or "unknown",
            "operation": operation or "unknown",
        })
        if len(events) > MAX_EVENTS:
            events.pop(0)
    for i, event in enumerate(events, start=1):
        event["sample_id"] = f"manual-{i:02d}"
    partial = truncated or oversized
    return {
        "schema": "cifereca-manual-log/1",
        "source": "user_selected_local_log",
        "coverage": "partial_sample" if partial else "bounded_sample",
        "sampled_lines": len(lines),
        "matched_lines": matched,
        "truncated": partial,
        "events": events,
    }


def read_selected_log(path: str) -> dict[str, object]:
    """Read only the last 32 KiB of one explicit, regular, local log file.

    The UI must pass an opt-in file chooser's local path. No directory walk,
    background collection, HTTP request, archival copy, or file name export.
    O_NONBLOCK prevents hangs on special files; fstat rejects them.
    """
    if type(path) is not str or not path or len(path) > 4096:
        raise ValueError("selected_log_invalid")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NONBLOCK", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except (OSError, ValueError) as exc:
        raise ValueError("selected_log_unavailable") from exc
    try:
        size = os.fstat(fd)
        if not stat.S_ISREG(size.st_mode):
            raise ValueError("selected_log_not_regular")
        offset = max(0, size.st_size - MAX_READ_BYTES)
        os.lseek(fd, offset, os.SEEK_SET)
        raw = os.read(fd, MAX_READ_BYTES)
        if offset:
            # The tail may begin mid-record/mid-Unicode. Skip the first
            # incomplete record before strict decoding; never reconstruct it.
            mark = raw.find(b"\n")
            raw = raw[mark + 1:] if mark >= 0 else b""
        return summarize_selected_log_bytes(raw, truncated=bool(offset))
    except OSError as exc:
        raise ValueError("selected_log_unavailable") from exc
    finally:
        os.close(fd)


def public_selected_log_evidence(value: object) -> dict[str, object]:
    """Fail closed for any caller-supplied or tampered projection.

    Explicitly reject arbitrary metadata before an optional model preview.
    """
    if type(value) is not dict or set(value) != _PUBLIC_KEYS:
        raise ValueError("selected_log_unsafe")
    if value["schema"] != "cifereca-manual-log/1" or value["source"] != "user_selected_local_log":
        raise ValueError("selected_log_unsafe")
    if value["coverage"] not in ("partial_sample", "bounded_sample"):
        raise ValueError("selected_log_unsafe")
    if type(value["truncated"]) is not bool or (
        value["truncated"] != (value["coverage"] == "partial_sample")
    ):
        raise ValueError("selected_log_unsafe")
    sampled, matched, rows = value["sampled_lines"], value["matched_lines"], value["events"]
    if not (
        type(sampled) is int and 0 <= sampled <= MAX_LINES
        and type(matched) is int and 0 <= matched <= sampled
        and type(rows) is list and len(rows) <= min(MAX_EVENTS, matched)
    ):
        raise ValueError("selected_log_unsafe")
    clean = []
    for index, event in enumerate(rows, start=1):
        if type(event) is not dict or set(event) != {
            "severity", "component", "operation", "sample_id",
        }:
            raise ValueError("selected_log_unsafe")
        if event["sample_id"] != f"manual-{index:02d}" or any(
            type(event[k]) is not str or event[k] not in values
            for k, values in (
                ("severity", (*_SEVERITIES, "unknown")),
                ("component", (*_COMPONENTS, "unknown")),
                ("operation", (*_OPERATIONS, "unknown")),
            )
        ):
            raise ValueError("selected_log_unsafe")
        clean.append(dict(event))
    return {
        "schema": "cifereca-manual-log/1",
        "source": "user_selected_local_log",
        "coverage": value["coverage"],
        "sampled_lines": sampled,
        "matched_lines": matched,
        "truncated": value["truncated"],
        "events": clean,
    }
