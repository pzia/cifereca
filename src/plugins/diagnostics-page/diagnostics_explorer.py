"""Presentation-only diagnostics explorer on the public, bounded Core broker.

All event values are already redacted by the Core. Do not access log files,
start providers, or claim that matching message text proves task correlation.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import Any
import json
import re

def local_timestamp_label(value: object) -> str:
    """Present a broker UTC instant in the machine's local offset, GUI only.

    Export/copy retains the canonical UTC timestamp. A malformed timestamp is
    never interpolated as free text into the interface or a diagnostic log.
    """
    if type(value) is not str or not 1 <= len(value) <= 64:
        return "Horodatage indisponible"
    try:
        instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if instant.tzinfo is None:
            return "Horodatage indisponible"
        return instant.astimezone().isoformat(
            sep=" ", timespec="milliseconds",
        ) + " (heure locale)"
    except (ValueError, OverflowError, OSError):
        return "Horodatage indisponible"


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
# The Core event broker currently publishes exactly these fixed source IDs
# and reason codes. Free-form source labels and reasons can contain names
# and titles that regex redaction cannot reliably recognise.
PUBLIC_SOURCE_REASONS = {
    "app_log": frozenset({"", "log_tail_bounded", "log_source_incomplete", "no_matching_events"}),
    "core_events": frozenset({
        "", "diagnostics_disabled", "snapshot_unavailable", "no_recorded_events",
        "snapshot_records_incomplete",
    }),
}


def public_source_evidence(raw: Mapping[str, Any]) -> dict[str, str]:
    """Keep only typed Core coverage metadata for GTK and AI previews.

    This is an exact allowlist, not an inference of remote or provider health.
    Unknown broker values are neither stringified nor included in UI/LLM
    reports, even when they contain no recognisable regex secret marker.
    """
    source = raw.get("source")
    source_id = source if type(source) is str and source in PUBLIC_SOURCE_REASONS else "inconnue"
    status = raw.get("status")
    status_id = (
        status if type(status) is str and status in SOURCE_STATES
        else "unavailable"
    )
    reason = raw.get("reason")
    reason_code = (
        reason
        if type(reason) is str and reason in PUBLIC_SOURCE_REASONS.get(source_id, ())
        else ""
    )
    return {"source": source_id, "status": status_id, "reason": reason_code}


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
    reference_time: datetime | None = None,
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
        severity=severity, since=explorer_since(period, now=reference_time), until=None,
        limit=PAGE_SIZE, offset=offset,
    )


def sanitized_rows(payload: Mapping[str, Any], *, search: str = "",
                   newest_first: bool = True, include_detail: bool = False) -> list[dict[str, Any]]:
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
        # Only explicitly published Core references may link observations.
        # Never extract one from a timestamp, event ID or message text.
        reference = published_task_reference(raw)
        if reference is not None:
            safe["task_ref"] = reference
        # Full text search is explicitly local to this 50-item broker page.
        if search and search.casefold() not in " ".join(
            safe[key] for key in ("timestamp", "source", "module", "severity", "message", "status")
        ).casefold():
            continue
        # Detailed GTK inspection is opt-in. AI previews keep the minimal
        # operational projection and must never inherit arbitrary detail.
        if include_detail is True and "detail" in raw:
            safe["detail"] = redacted_detail(raw["detail"])
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


def published_task_reference(row: Mapping[str, Any]) -> str | None:
    """Consume an explicit public broker field; never manufacture provenance.

    Core stores task_ref at top level on worker outcomes and in detail on
    provider events. Contradictory or malformed references fail closed.
    """
    source = row.get("source")
    if type(source) is not str or source not in ("core_events", "app_log"):
        return None
    values = []
    if "task_ref" in row:
        values.append(row["task_ref"])
    detail = row.get("detail")
    if isinstance(detail, Mapping) and "task_ref" in detail:
        values.append(detail["task_ref"])
    if not values or any(type(value) is not str or
                         re.fullmatch(r"[0-9a-f]{12}", value) is None
                         for value in values):
        return None
    return values[0] if all(value == values[0] for value in values) else None


def linked_operation_groups(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group this page by explicit worker scope, not by apparent causality.

    Unreferenced rows remain individual observations even at the same instant.
    Provider/worker statuses and duplicate observations are kept independently.
    """
    groups: list[dict[str, Any]] = []
    by_reference: dict[str, dict[str, Any]] = {}
    for row in rows[:PAGE_SIZE]:
        if not isinstance(row, dict):
            continue
        # sanitized_rows has already resolved/rejected contradictory origins.
        # Do not reconsider retained display detail and revive a rejected ref.
        reference = published_task_reference({
            "source": row.get("source"), "task_ref": row.get("task_ref"),
        })
        group = by_reference.get(reference) if reference is not None else None
        if group is None:
            group = {"task_ref": reference, "events": [],
                     "label": (f"Tâche {reference} · référence Core publiée"
                               if reference else "Corrélation non disponible")}
            groups.append(group)
            if reference is not None:
                by_reference[reference] = group
        group["events"].append(row)
    for group in groups:
        levels = {row.get("severity") for row in group["events"]}
        group["severity"] = next((level for level in _LEVELS if level in levels), "unknown")
        group["count"] = len(group["events"])
    return groups


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
        evidence = public_source_evidence(raw)
        status = evidence["status"]
        state = {
            "source": evidence["source"],
            "status": status,
            "label": SOURCE_STATES[status],
        }
        reason = evidence["reason"]
        # Only Core's fixed operational reason codes may be published.
        # 'available' never advertises a failure reason.
        if reason and status in ("empty", "disabled", "unavailable", "partial"):
            state["reason"] = reason
        if status == "partial" and evidence["source"] == "core_events" and (
            reason == "snapshot_records_incomplete"
        ):
            state["label"] = "Historique structuré partiel (rejets / limite)"
        # Dates are metadata from the Core, never arbitrary display strings.
        # Validate timezone and normalise UTC; do not stringify hostile types.
        raw_last = raw.get("last_event")
        # A missing or disabled source cannot establish the time of its
        # latest published event. An unknown source also has no attestable
        # provenance under the current two-source Core contract.
        if (
            evidence["source"] != "inconnue"
            and status in ("available", "partial")
            and type(raw_last) is str and len(raw_last) <= 40
        ):
            try:
                instant = datetime.fromisoformat(raw_last.replace("Z", "+00:00"))
                if instant.tzinfo is not None:
                    state["last_event"] = instant.astimezone(timezone.utc).isoformat(
                        timespec="seconds",
                    )
            except (ValueError, OverflowError):
                pass
        states.append(state)
    return states



_CHANNEL_LABELS = {
    "core_events": "Événements Core (mémoire)",
    "app_log": "Journal applicatif (lecture bornée)",
    "inconnue": "Canal non identifié",
}
_CHANNEL_REASON_LABELS = {
    "log_tail_bounded": "Lecture volontairement bornée : lignes anciennes possibles hors extrait",
    "log_source_incomplete": "Journal incomplet ou partiellement inaccessible",
    "no_matching_events": "Aucune ligne technique admissible dans cet extrait",
    "diagnostics_disabled": "Collecteur Core désactivé",
    "snapshot_unavailable": "Événements Core indisponibles",
    "no_recorded_events": "Aucun événement Core enregistré",
    "snapshot_records_incomplete": "Historique mémoire borné ou événements écartés",
}


def diagnostic_channel_caption(payload: Mapping[str, Any]) -> str:
    """Explain channel availability, never account/provider connection health.

    Labels and reasons come exclusively from the closed, validated Core broker
    vocabulary. No source name, exception or reason supplied by a plugin is
    stringified into GTK or an AI/export payload.
    """
    states = source_summaries(payload)
    if not states:
        return (
            "Canaux de diagnostic : états indisponibles "
            "(événements Core / journal applicatif)."
        )
    parts = []
    for state in states:
        label = _CHANNEL_LABELS[state["source"]]
        status_label = state["label"]
        if state["source"] == "core_events" and state["status"] == "partial" and (
            state.get("reason") != "snapshot_records_incomplete"
        ):
            # A bounded in-memory snapshot cannot be explained by log-file
            # rotation. Never call that state a truncated journal.
            status_label = "Historique Core partiel (couverture incomplète)"
        item = f"{label} : {status_label}"
        reason = state.get("reason")
        if reason:
            item += " — " + _CHANNEL_REASON_LABELS.get(reason, "Raison non disponible")
        if state.get("last_event"):
            item += " · dernier événement publié : " + local_timestamp_label(
                state["last_event"]
            )
        parts.append(item)
    return (
        "Canaux de diagnostic — " + " · ".join(parts)
        + ". Ces états ne décrivent pas la connexion aux fournisseurs."
    )


def explorer_header(payload: Mapping[str, Any], rows: list[dict[str, str]],
                    *, search: str = "") -> str:
    if not isinstance(payload, Mapping):
        return "Canaux de diagnostic indisponibles — aucune donnée exploitable"
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
        return "Canaux de diagnostic indisponibles — aucune donnée exploitable"
    suffix = " · recherche dans cette page uniquement" if search else ""
    return f"{shown} événements affichés · {total} disponibles · page à partir de {offset + 1}{suffix}"


def empty_observation(payload: Mapping[str, Any], *, period: str,
                      severity: str = "", module: str = "", search: str = "") -> str:
    """A filtered empty feed is neither a health check nor complete coverage."""
    labels = {"all": "tout l’historique disponible", "hour": "dernière heure",
              "day": "24 heures", "week": "7 jours", "month": "30 jours"}
    states = source_summaries(payload)
    if not states or not any(s["status"] in {"available", "empty", "partial"} for s in states):
        return "Aucune donnée exploitable dans les sources consultées."
    total = payload.get("total")
    if type(total) is not int or total != 0:
        return ""
    window = labels.get(period, "période inconnue")
    # A filtered empty page is not evidence of an all-clear; the selection
    # might simply exclude the relevant domain, severity or local search.
    # Preserve the distinction between an empty complete feed and a partial
    # source window (log rotation, missing privileges, disabled Core).
    incomplete = (
        payload.get("status") in ("partial", "unavailable")
        or any(s["status"] not in {"available", "empty"} for s in states)
    )
    if severity or module or search:
        message = f"Aucun événement correspondant aux filtres · période : {window}."
        if incomplete:
            message += " Couverture partielle ; événements manquants possibles."
        return message
    if incomplete:
        return (
            f"Aucun événement publié dans l’extrait consultable · période : {window}. "
            "Couverture partielle ; les sources indisponibles ne sont pas évaluées "
            "et les journaux tronqués peuvent masquer des événements."
        )
    return (f"Pas d’erreur observée dans les sources consultées · période : {window}. "
            "Cette observation ne garantit pas la disponibilité des services.")


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


def selectable_event_ids(rows: list[dict[str, str]], *, max_rows: int = PAGE_SIZE) -> set[str]:
    """Only distinct, nonempty event references identify a selectable row.

    Ordinary GTK page operations stay at 50. The explicit, background-only
    filtered selection can validate at most 200 redacted rows separately.
    """
    if type(max_rows) is not int or not PAGE_SIZE <= max_rows <= 200:
        raise ValueError("invalid diagnostics selection limit")
    counts: dict[str, int] = {}
    for row in rows[:max_rows]:
        if not isinstance(row, dict):
            continue
        event_id = row.get("event_id")
        if type(event_id) is str and event_id:
            counts[event_id] = counts.get(event_id, 0) + 1
    return {event_id for event_id, count in counts.items() if count == 1}


def export_selected_events(
    rows: list[dict[str, str]], event_ids: set[str], *,
    max_events: int = PAGE_SIZE, max_bytes: int = 24_000,
) -> str:
    """Export the COMPLETE, redacted selection or fail, never truncate.

    Defaults preserve the 50-row / 24 KB GTK page contract. Only the
    background filtered scan opts into the 200-row / 96 KB batch budget.
    The caller cannot raise either budget above those hard safety caps.
    """
    from cifereca_diagnostics_page import redacted_detail
    if (type(max_events) is not int or not PAGE_SIZE <= max_events <= 200
            or type(max_bytes) is not int or not 1 <= max_bytes <= 96_000):
        raise ValueError("invalid diagnostic export budget")
    if len(event_ids) > max_events or len(rows) > max_events:
        raise ValueError("too many selected events for one export")
    if not event_ids.issubset(selectable_event_ids(rows, max_rows=max_events)):
        raise ValueError("ambiguous or unidentified event selection")
    fields = ("timestamp", "source", "module", "severity", "message", "status", "event_id")
    selected = []
    for row in rows:
        if (not isinstance(row, dict) or type(row.get("event_id")) is not str
                or row["event_id"] not in event_ids):
            continue
        item = {
            field: redacted_detail(value)[:500] if isinstance(value, str) else ""
            for field in fields
            for value in (row.get(field),)
        }
        reference = published_task_reference({
            "source": row.get("source"), "task_ref": row.get("task_ref"),
        })
        if reference is not None:
            item["task_ref"] = reference
        selected.append(item)
    if len(selected) > max_events:
        raise ValueError("too many selected events for one export")
    result = json.dumps({
        "notice": "Événements expurgés du Core ; corrélation limitée aux task_ref "
                  "publiées. Étapes potentiellement "
                  "absentes ; aucune causalité déduite de l'heure ou des messages.",
        "events": selected,
    }, ensure_ascii=False, indent=2, allow_nan=False)
    try:
        size = len(result.encode("utf-8"))
    except UnicodeError as exc:
        raise ValueError("diagnostic export contains invalid Unicode") from exc
    if size > max_bytes:
        raise ValueError(f"diagnostic export exceeds the {max_bytes // 1000} KB limit")
    return result
