"""Closed, redacted GTK layout-critical counters for Core E2E (#2027).

Do not serialize GTK log lines, addresses, window/account titles or raw CSS.
Only two named widget families from the reported geometry incident qualify.
"""
from __future__ import annotations

import re

_MAX_STREAM = 1_000_000
_CRITICAL = re.compile(r"(?i)gtk-critical")
_STACK = re.compile(r"(?i)\b(?:GtkStack|gtk_stack)\b")
_REVEALER = re.compile(r"(?i)\b(?:GtkRevealer|gtk_revealer)\b")


def gtk_layout_critical_counts(*streams: str) -> dict[str, int | bool]:
    stack = revealer = 0
    truncated = False
    for stream in streams:
        if not isinstance(stream, str):
            continue
        truncated = truncated or len(stream) > _MAX_STREAM
        for line in stream[:_MAX_STREAM].splitlines():
            if not _CRITICAL.search(line):
                continue
            # Count a single affected family once per line; ignore all other
            # criticals because #2027 is specific to Stack/Revealer allocations.
            stack += bool(_STACK.search(line))
            revealer += bool(_REVEALER.search(line))
    return {"GtkStack": stack, "GtkRevealer": revealer, "stream_truncated": truncated}


def gtk_layout_critical_failure(counts: dict[str, int | bool]) -> str | None:
    """Constant-vocabulary failure for CI; no excerpts from stderr."""
    stack = counts.get("GtkStack", 0)
    revealer = counts.get("GtkRevealer", 0)
    if type(stack) is not int or type(revealer) is not int:
        return "GTK layout critical counters invalides"
    if stack or revealer:
        return f"GTK layout P0 #2027 : GtkStack={stack}, GtkRevealer={revealer}"
    # Do not assert that truncated streams prove absence of warnings.
    if counts.get("stream_truncated") is True:
        return "GTK layout P0 #2027 : journal tronqué, absence non attestée"
    return None


# Only reviewed, synthetic GTK acceptance phases. A plugin-supplied name, URL,
# widget address, title or arbitrary stderr content is never a published key.
GTK_LAYOUT_PHASES = frozenset((
    "pre_phase", "shell_ready", "sync_narrow", "numilog_consent",
    "numilog_dialog", "maintenance_begin", "maintenance_preview",
    "maintenance_execute", "maintenance_finish",
))
_PHASE_LINE = re.compile(
    r"^CIFERECA_E2E_GTK_PHASE:(shell_ready|sync_narrow|numilog_consent|"
    r"numilog_dialog|maintenance_begin|maintenance_preview|"
    r"maintenance_execute|maintenance_finish)$"
)


def gtk_layout_phase_counts(stderr: str) -> dict[str, dict[str, int]]:
    """Count native GTK criticals between trusted fixture phase markers.

    This is ordering evidence within one process stderr stream, not an
    assertion that a Python callback caused a native layout warning. The
    runner still records the global totals and stream_truncated separately.
    """
    if not isinstance(stderr, str):
        return {}
    phase = "pre_phase"
    grouped: dict[str, dict[str, int]] = {}
    for line in stderr[:_MAX_STREAM].splitlines():
        marker = _PHASE_LINE.fullmatch(line)
        if marker:
            phase = marker.group(1)
            continue
        if not _CRITICAL.search(line):
            continue
        stack = bool(_STACK.search(line))
        revealer = bool(_REVEALER.search(line))
        if not stack and not revealer:
            continue
        bucket = grouped.setdefault(phase, {"GtkStack": 0, "GtkRevealer": 0})
        bucket["GtkStack"] = min(100000, bucket["GtkStack"] + stack)
        bucket["GtkRevealer"] = min(100000, bucket["GtkRevealer"] + revealer)
    return grouped
