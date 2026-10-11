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
    if now is not None and not isinstance(now, datetime):
        raise ValueError("invalid diagnostic reference time")
    current = datetime.now(timezone.utc) if now is None else now
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
    if not isinstance(source, str) or not isinstance(severity, str) or not isinstance(period, str):
        raise ValueError("unsupported diagnostics filter")
    if source not in SOURCES or severity not in SEVERITIES or period not in PERIODS:
        raise ValueError("unsupported diagnostics filter")
    if not isinstance(module, str) or len(module) > 90:
        raise ValueError("invalid diagnostics module")
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
    if not isinstance(search, str):
        raise ValueError("invalid diagnostics search")
    if not isinstance(newest_first, bool):
        raise ValueError("invalid diagnostics sort order")
    # Redact the complete broker value before applying the UI's display limit.
    # Cutting first could turn token=PRIVATE into token=PRI and expose a prefix.
    from cifereca_diagnostics_page import redacted_detail
    if not isinstance(search, str) or len(search) > 500:
        raise ValueError("invalid diagnostics search")
    results: list[dict[str, str]] = []
    events = payload.get("events")
    if not isinstance(events, (list, tuple)):
        return []
    for raw in events[:PAGE_SIZE]:
        if not isinstance(raw, Mapping):
            continue
        safe = {
            key: redacted_detail(raw[key])[:500] if isinstance(raw.get(key), str) else ""
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
    for row in rows[:PAGE_SIZE]:
        if not isinstance(row, dict):
            continue
        module = row.get("module")
        source = row.get("source")
        key = (
            module[:90] if isinstance(module, str) and module else "Inconnu",
            source[:40] if isinstance(source, str) and source else "Inconnue",
        )
        groups.setdefault(key, []).append(row)
    answer = []
    for (module, source), items in sorted(groups.items(), key=lambda pair: pair[0]):
        levels = {item.get("severity") for item in items if isinstance(item.get("severity"), str)}
        severity = next((level for level in _LEVELS if level in levels), "unknown")
        answer.append({
            "module": module, "source": source, "count": len(items),
            "severity": severity, "events": items,
        })
    return answer


def source_summaries(payload: Mapping[str, Any]) -> list[dict[str, str]]:
    states = []
    if not isinstance(payload, Mapping):
        return states
    sources = payload.get("sources")
    if not isinstance(sources, (list, tuple)):
        return states
    for raw in sources[:4]:
        if not isinstance(raw, Mapping):
            continue
        status = raw.get("status")
        if not isinstance(status, str):
            status = "unavailable"
        states.append({
            "source": raw["source"][:40] if isinstance(raw.get("source"), str) else "inconnue",
            "status": status if status in SOURCE_STATES else "unavailable",
            "label": SOURCE_STATES.get(status, SOURCE_STATES["unavailable"]),
        })
    return states


def explorer_header(payload: Mapping[str, Any], rows: list[dict[str, str]],
                    *, search: str = "") -> str:
    if not isinstance(payload, Mapping):
        return "Sources non alimentées — aucune donnée disponible"
    def bounded_count(value: Any) -> int:
        # The broker contract uses numeric counts. Do not call __int__
        # on arbitrary objects supplied by a malformed broker response.
        if type(value) is int:
            return min(10000, max(0, value))
        if type(value) is str and len(value) <= 6 and value.isdecimal():
            return min(10000, int(value))
        return 0

    total = bounded_count(payload.get("total"))
    offset = bounded_count(payload.get("offset"))
    shown = len(rows)
    if not payload.get("sources"):
        return "Sources non alimentées — aucune donnée disponible"
    suffix = " · recherche dans cette page uniquement" if search else ""
    return f"{shown} événements affichés · {total} disponibles · page à partir de {offset + 1}{suffix}"


def next_page_offset(payload: Mapping[str, Any], *, current_offset: int) -> int | None:
    """Only a monotonically increasing, bounded broker cursor is navigable."""
    if not isinstance(payload, Mapping) or type(current_offset) is not int:
        return None
    returned_offset = payload.get("offset")
    following = payload.get("next_offset")
    if type(returned_offset) is not int or returned_offset != current_offset:
        return None
    if type(following) is not int:
        return None
    total = payload.get("total")
    if type(total) is not int or total < 0:
        return None
    if current_offset < following <= min(10000, current_offset + PAGE_SIZE) and following < total:
        return following
    return None


def selectable_event_ids(rows: list[dict[str, str]]) -> set[str]:
    """Only distinct, nonempty event references identify a selectable row."""
    counts: dict[str, int] = {}
    for row in rows[:PAGE_SIZE]:
        if not isinstance(row, dict):
            continue
        event_id = row.get("event_id")
        if type(event_id) is str and event_id:
            counts[event_id] = counts.get(event_id, 0) + 1
    return {event_id for event_id, count in counts.items() if count == 1}


def export_selected_events(rows: list[dict[str, str]], event_ids: set[str]) -> str:
    """Export the COMPLETE selected page or fail explicitly; never truncate."""
    from cifereca_diagnostics_page import redacted_detail
    if len(event_ids) > PAGE_SIZE:
        raise ValueError("too many selected events for one export")
    if not event_ids.issubset(selectable_event_ids(rows)):
        raise ValueError("ambiguous or unidentified event selection")
    fields = ("timestamp", "source", "module", "severity", "message", "status", "event_id")
    selected = [
        {
            field: redacted_detail(value)[:500] if isinstance(value, str) else ""
            for field in fields
            for value in (row.get(field),)
        }
        for row in rows
        if isinstance(row, dict) and isinstance(row.get("event_id"), str)
        and row["event_id"] in event_ids
    ]
    if len(selected) > PAGE_SIZE:
        raise ValueError("too many selected events for one export")
    result = json.dumps({
        "notice": "Événements expurgés du Core ; pas de corrélation de tâches prouvée",
        "events": selected,
    }, ensure_ascii=False, indent=2, allow_nan=False)
    try:
        size = len(result.encode("utf-8"))
    except UnicodeError as exc:
        raise ValueError("diagnostic export contains invalid Unicode") from exc
    if size > 24_000:
        raise ValueError("diagnostic export exceeds the 24 KB limit")
    return result
