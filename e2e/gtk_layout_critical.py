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


# This gate becomes enforceable only after product fix #2466 yielded 0/0
# on both MainWindow baselines and both Maintenance reproductions.
# Other scenarios retain observational telemetry, not an implicit new gate.
GTK_LAYOUT_ZERO_GATE_2027 = frozenset({
    "mainwindow-baseline-1366", "mainwindow-baseline-1600",
    "maintenance-tools-1280", "maintenance-tools-1366", "maintenance-tools-1600",
})


def gtk_layout_zero_gate(scenario: str, counts: dict[str, int | bool]) -> str | None:
    """Fail closed on known #2027 reproductions, never expose raw GTK logs."""
    if type(scenario) is not str or scenario not in GTK_LAYOUT_ZERO_GATE_2027:
        return None
    return gtk_layout_critical_failure(counts)


# Marker values are literal constants emitted only by the reviewed E2E probe.
# The tool never republishes the underlying GTK log or arbitrary marker text.
GTK_PHASES_2027 = (
    "window_start", "window_shown", "sync_desktop",
    "sync_font_desktop", "sync_narrow", "sync_font_narrow",
    "sync_restored", "numilog_consent", "done",
    # The Maintenance probe uses the same privacy-safe marker vocabulary.
    "maint_window_start", "maint_window_shown", "maint_route",
    "maint_busy_guard", "maint_scan", "maint_first_dialog",
    "maint_cancel_stale", "maint_execute", "maint_refresh",
    "maint_p1_naming", "maint_p1_duplicates", "maint_done",
    "maint_dialog_visible", "maint_dialog_response",
    "maint_p1_dialog_visible", "maint_p1_dialog_response",
)
_PHASE_MARKER = re.compile(
    r"^CIFERECA_E2E_GTK_PHASE:(?P<phase>[a-z0-9_]+)$"
)


def gtk_layout_phase_counts(*streams: str) -> dict[str, dict[str, int]]:
    """Attribute GTK criticals to reviewed phases in either capture stream.

    GTK native log routing can differ by runner/runtime: count each stream
    independently, provided both receive the same *literal* marker set.
    No raw line, pointer, user data or untrusted phase is emitted.
    """
    counts = {key: {"GtkStack": 0, "GtkRevealer": 0} for key in GTK_PHASES_2027}
    for stream in streams:
        if type(stream) is not str:
            continue
        active = None
        for line in stream[:_MAX_STREAM].splitlines():
            clean = line.strip()
            if clean.startswith("CIFERECA_E2E_GTK_PHASE:"):
                # Any marker-looking line (including invalid syntax) ends the
                # previous phase; never borrow counts from a known phase.
                marker = _PHASE_MARKER.fullmatch(clean)
                active = (marker.group("phase") if marker is not None
                          and marker.group("phase") in counts else None)
                continue
            if active is None or not _CRITICAL.search(line):
                continue
            counts[active]["GtkStack"] = min(
                100000, counts[active]["GtkStack"] + bool(_STACK.search(line))
            )
            counts[active]["GtkRevealer"] = min(
                100000, counts[active]["GtkRevealer"] + bool(_REVEALER.search(line))
            )
    return {key: value for key, value in counts.items()
            if value["GtkStack"] or value["GtkRevealer"]}
