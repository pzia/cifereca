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


# Marker values are literal constants emitted only by the reviewed E2E probe.
# The tool never republishes the underlying GTK log or arbitrary marker text.
GTK_PHASES_2027 = (
    "window_start", "window_shown", "sync_desktop",
    "sync_font_desktop", "sync_narrow", "sync_font_narrow",
    "sync_restored", "numilog_consent", "done",
)
_PHASE_MARKER = re.compile(
    r"^CIFERECA_E2E_GTK_PHASE:(?P<phase>[a-z_]+)$"
)


def gtk_layout_phase_counts(stderr: str) -> dict[str, dict[str, int]]:
    """Closed phase-attribution counts; stderr must come from one E2E process.

    This is diagnostic correlation rather than proof of causation or an
    entitlement to emit arbitrary provider error data. Only two widget
    families and the exact nine source-reviewed phases can appear.
    """
    if type(stderr) is not str:
        return {}
    counts = {key: {"GtkStack": 0, "GtkRevealer": 0} for key in GTK_PHASES_2027}
    active = None
    for line in stderr[:_MAX_STREAM].splitlines():
        marker = _PHASE_MARKER.fullmatch(line.strip())
        if marker is not None and marker.group("phase") in counts:
            active = marker.group("phase")
            continue
        if active is None or not _CRITICAL.search(line):
            continue
        counts[active]["GtkStack"] += bool(_STACK.search(line))
        counts[active]["GtkRevealer"] += bool(_REVEALER.search(line))
    return {key: value for key, value in counts.items()
            if value["GtkStack"] or value["GtkRevealer"]}
