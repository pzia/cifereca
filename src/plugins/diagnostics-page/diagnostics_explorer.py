"""Presentation-only diagnostics explorer on the public, bounded Core broker.

All event values are already redacted by the Core. Do not access log files,
start providers, or claim that matching message text proves task correlation.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import Any
import json

SOURCES = ("all", "core_events", "app_log")
SEVERITIES = ("", "debug", "info", "warning", "error", "critical")
PERIODS = ("all", "hour", "day", "week", "month")
PAGE_SIZE = 50
SOURCE_STATES = {
    "available": "Alimentée",
    "empty": "Pas d’événement enregistré",
    "disabled": "Désactivée",
    "unavailable": "Indisponible",
    "partial": "Journal partiel (rotation / troncation)",
}
_LEVELS = ("critical", "error", "warning", "info", "debug")


def explorer_since(period: str, *, now: datetime | None = None) -> str | None:
    """Return a timezone-aware ISO start time for a broker query."""
    if period not in PERIODS:
        raise ValueError("unknown diagnostic period")
    if period == "all":
        return None
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("timezone required")
    delta = {
        "hour": timedelta(hours=1),
        "day": timedelta(days=1),
        "week": timedelta(days=7),
        "month": timedelta(days=30),
    }[period]
    return (current.astimezone(timezone.utc) - delta).isoformat()


def fetch_explorer_page(
    broker: Any, *, source: str = "all", module: str = "",
    severity: str = "", period: str = "day", offset: int = 0,
) -> dict[str, Any]:
    """Only public diagnostics.events; never a path or arbitrary log query."""
    if source not in SOURCES or severity not in SEVERITIES:
        raise ValueError("unsupported diagnostics filter")
    if not isinstance(offset, int) or isinstance(offset, bool) or not 0 <= offset <= 10000:
        raise ValueError("invalid diagnostics offset")
    return broker.events(
        source=source, module=module.strip()[:90],
        severity=severity, since=explorer_since(period), until=None,
        limit=PAGE_SIZE, offset=offset,
    )


def sanitized_rows(payload: Mapping[str, Any], *, search: str = "",
                   newest_first: bool = True) -> list[dict[str, str]]:
    """Normalize only Core-published fields; omit unexpected keys entirely."""
    if not isinstance(payload, Mapping):
        return []
    results: list[dict[str, str]] = []
    for raw in tuple(payload.get("events") or ())[:PAGE_SIZE]:
        if not isinstance(raw, Mapping):
            continue
        safe = {
            key: str(raw.get(key) or "")[:500]
            for key in ("timestamp", "source", "module", "severity", "message", "status", "event_id")
        }
        # Full text search is explicitly local to this 50-item broker page.
        if search and search.casefold() not in " ".join(
            safe[key] for key in ("timestamp", "source", "module", "severity", "message", "status")
        ).casefold():
            continue
        results.append(safe)
    def chronological_key(row: dict[str, str]) -> tuple[datetime, str]:
        try:
            instant = datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00"))
            if instant.tzinfo is None:
                raise ValueError("missing timezone")
            instant = instant.astimezone(timezone.utc)
        except (ValueError, OverflowError):
            instant = datetime.min.replace(tzinfo=timezone.utc)
        return instant, row["event_id"]

    results.sort(key=chronological_key, reverse=newest_first)
    return results


def module_groups(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    """Hierarchy by module+source with severity/count; no inferred correlation."""
    groups: dict[tuple[str, str], list[dict[str, str]]] = {}
    for row in rows:
        key = (row.get("module") or "Inconnu", row.get("source") or "Inconnue")
        groups.setdefault(key, []).append(row)
    answer = []
    for (module, source), items in sorted(groups.items(), key=lambda pair: pair[0]):
        levels = {item["severity"] for item in items}
        severity = next((level for level in _LEVELS if level in levels), "unknown")
        answer.append({
            "module": module, "source": source, "count": len(items),
            "severity": severity, "events": items,
        })
    return answer


def source_summaries(payload: Mapping[str, Any]) -> list[dict[str, str]]:
    states = []
    for raw in tuple(payload.get("sources") or ())[:4]:
        if not isinstance(raw, Mapping):
            continue
        status = str(raw.get("status") or "unavailable")
        states.append({
            "source": str(raw.get("source") or "inconnue")[:40],
            "status": status if status in SOURCE_STATES else "unavailable",
            "label": SOURCE_STATES.get(status, SOURCE_STATES["unavailable"]),
        })
    return states


def explorer_header(payload: Mapping[str, Any], rows: list[dict[str, str]],
                    *, search: str = "") -> str:
    total = max(0, int(payload.get("total") or 0))
    offset = max(0, int(payload.get("offset") or 0))
    shown = len(rows)
    if not payload.get("sources"):
        return "Sources non alimentées — aucune donnée disponible"
    suffix = " · recherche dans cette page uniquement" if search else ""
    return f"{shown} événements affichés · {total} disponibles · page à partir de {offset + 1}{suffix}"


def export_selected_events(rows: list[dict[str, str]], event_ids: set[str]) -> str:
    """Export only selected public fields and provenance; never raw logs."""
    from cifereca_diagnostics_page import format_diagnostic_detail
    fields = ("timestamp", "source", "module", "severity", "message", "status", "event_id")
    selected = [
        {field: str(row.get(field) or "")[:500] for field in fields}
        for row in rows if row.get("event_id") in event_ids
    ][:PAGE_SIZE]
    return format_diagnostic_detail(
        {"notice": "Événements expurgés du Core ; pas de corrélation de tâches prouvée",
         "events": selected},
        "json",
    )
