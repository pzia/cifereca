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
