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
import unicodedata

from diagnostics_reconciliation import (
    reconciliation_event_summary,
    exportable_reconciliation_aggregates, sanitized_reconciliation_export,
)

# Fixed Core-owned cover failure grammar (#3108). These fields identify
# observed journal records only; they do not prove unique HTTP requests,
# numerical status codes, a provider, a title or a borrower.
_COVER_FAILURE_MESSAGES = {
    "timeout": "couverture · délai dépassé",
    "http_status": "couverture · échec HTTP",
    "request_failed": "couverture · requête échouée",
    "storage_failed": "couverture · écriture échouée",
    "invalid_image": "couverture · image invalide",
    "unexpected": "couverture · erreur inattendue",
}


def published_cover_failure(raw: Mapping[str, Any]) -> str | None:
    """Recognize only the dedicated Core observation's strict envelope."""
    if not isinstance(raw, Mapping):
        return None
    source = raw.get("source")
    if type(source) is not str or source not in {"journal", "core_events"}:
        # app_log/TRACE are not a second failed HTTP attempt.
        return None
    if (raw.get("module") != "COVER" or raw.get("status") != "error"
            or raw.get("severity") != "error"):
        return None
    if source == "journal":
        if (raw.get("logger") != "cifereca"
                or raw.get("operation") != "cover_download_failure"):
            return None
    elif "operation" in raw:
        # The legacy Core projection never exports its internal operation.
        return None
    detail = raw.get("detail")
    if type(detail) is not dict or set(detail) != {"code"}:
        return None
    code = detail.get("code")
    if (type(code) is not str
            or raw.get("message") != _COVER_FAILURE_MESSAGES.get(code)):
        return None
    return code


def cover_failure_page_summary(rows: list[dict[str, Any]]) -> str:
    """Page-local count from attested rows, never a total of HTTP attempts."""
    counts = {code: 0 for code in _COVER_FAILURE_MESSAGES}
    for row in rows[:PAGE_SIZE]:
        if not isinstance(row, dict):
            continue
        code = row.get("cover_reason")
        if (type(code) is str and code in counts
                and row.get("module") == "COVER"
                and row.get("status") == "error"
                and row.get("message") == _COVER_FAILURE_MESSAGES[code]
                and type(row.get("source")) is str
                and row["source"] in {"journal", "core_events"}):
            counts[code] += 1
    total = sum(counts.values())
    if total == 0:
        return ""
    reasons = ", ".join(
        f"{_COVER_FAILURE_MESSAGES[code].split(' · ', 1)[1]} : {count}"
        for code, count in counts.items() if count
    )
    return (f" · Couvertures : {total} événement(s) observé(s) sur cette page"
            f" ({reasons}) · nombre de requêtes HTTP distinctes inconnu")


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


SOURCES = ("all", "core_events", "app_log", "journal")
SEVERITIES = ("", "debug", "info", "warning", "error", "critical")
PERIODS = ("all", "hour", "day", "week", "month")
PAGE_SIZE = 50
DOMAIN_LABELS = {
    "": "Tous domaines",
    "browser_recorder": "Browser Recorder",
    "reconciliation": "Réconciliation",
    "loan_download": "Téléchargements de prêts",
}


def normalize_diagnostic_domain(value: object) -> str:
    """Closed UI aliases; never pass arbitrary input to the Core broker.

    An unknown string MUST NOT silently downgrade the global filter to 'all'.
    Accent and one known spelling variant are deliberately accepted, not
    fuzzy-matched against private messages or source names.
    """
    if type(value) is not str or len(value) > 64:
        raise ValueError("unknown diagnostic domain")
    canonical = "".join(
        char for char in unicodedata.normalize("NFKD", value.strip().casefold())
        if not unicodedata.combining(char)
    ).replace("_", " ").replace("-", " ")
    canonical = " ".join(canonical.split())
    if canonical in ("",):
        return ""
    if canonical in ("browser", "recorder", "browser recorder"):
        return "browser_recorder"
    if canonical in ("reconciliation", "reconcilitation"):
        return "reconciliation"
    if canonical in (
        "download", "loan download", "download loan",
        "telechargement de pret", "telechargement du pret",
        "telechargements de prets",
    ):
        return "loan_download"
    raise ValueError("unknown diagnostic domain")


# Only Core canonical public provider labels, never account names or URLs.
_PUBLIC_PROVIDER_RE = re.compile(
    r"[a-z][a-z0-9]{0,31}(?:[._-][a-z][a-z0-9]{0,31}){0,5}\Z", re.ASCII,
)
_PRIVATE_PROVIDER_PARTS = frozenset({
    "secret", "token", "password", "cookie", "credential", "credentials",
    "account", "identity", "barcode", "session", "authorization", "bearer",
})


def public_provider_id(value: object) -> str | None:
    if (type(value) is not str or len(value) > 64
            or _PUBLIC_PROVIDER_RE.fullmatch(value) is None
            or any(segment in _PRIVATE_PROVIDER_PARTS
                   for segment in re.split(r"[._-]", value))):
        return None
    return value

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
    "journal": frozenset({"", "rotation_or_read_gap", "active_log_missing", "no_recorded_events"}),
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
    broker: Any, *, source: str = "journal", module: str = "",
    provider_id: str = "", domain: str = "",
    severity: str = "", period: str = "day", offset: int = 0,
    reference_time: datetime | None = None, reason_code: str = "",
    search: str = "",
) -> dict[str, Any]:
    """Only public diagnostics.events; never a path or arbitrary log query."""
    if not isinstance(source, str) or not isinstance(severity, str) or not isinstance(period, str):
        raise ValueError("unsupported diagnostics filter")
    if source not in SOURCES or severity not in SEVERITIES or period not in PERIODS:
        raise ValueError("unsupported diagnostics filter")
    if not isinstance(module, str) or len(module) > 90:
        raise ValueError("invalid diagnostics module")
    if type(provider_id) is not str or (provider_id and public_provider_id(provider_id) is None):
        raise ValueError("invalid diagnostics provider")
    verified_domain = normalize_diagnostic_domain(domain)
    if (type(reason_code) is not str
            or (reason_code and re.fullmatch(r"[a-z][a-z0-9_]{0,79}", reason_code) is None)):
        raise ValueError("invalid reconciliation reason code")
    if not isinstance(offset, int) or isinstance(offset, bool) or not 0 <= offset <= 1000000:
        raise ValueError("invalid diagnostics offset")
    query = {
        "source": source, "module": module.strip()[:90],
        "severity": severity, "since": explorer_since(period, now=reference_time),
        "until": None, "limit": PAGE_SIZE, "offset": offset,
    }
    if type(search) is not str or len(search) > 500:
        raise ValueError("invalid diagnostics search")
    if source == "journal":
        if reason_code or verified_domain:
            raise ValueError("raw journal does not attest a domain or reason code")
        # Only the canonical journal has a Core-side pre-pagination text search.
        query["search"] = search
    # Older Core versions can still serve the unfiltered page. Require the
    # optional new broker keyword ONLY for an explicitly selected code;
    # never silently downgrade a global filter to local 50-item search.
    if reason_code:
        query["reason_code"] = reason_code
    if provider_id:
        query["provider_id"] = provider_id
    if verified_domain:
        # Preserve compatibility with legacy brokers for the default 'all'
        # only. A selected business domain requires an exact server filter.
        query["domain"] = verified_domain
    return broker.events(**query)


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
        provider = public_provider_id(raw.get("provider_id"))
        domain = raw.get("domain")
        if (raw.get("source") == "core_events"
                and ((raw.get("module") == "PLUGIN" and domain == "browser_recorder")
                     or (raw.get("module") == "LIBRARY" and domain == "reconciliation")
                     or (raw.get("module") in ("PNB", "LOAN")
                         and domain == "loan_download"))):
            safe["domain"] = domain
        if provider is not None:
            safe["provider_id"] = provider
        cover_reason = published_cover_failure(raw)
        if cover_reason is not None:
            safe["cover_reason"] = cover_reason
        # The Core attests the terminal reconciliation event; present only
        # finite, local aggregate labels. The code is searchable on this page
        # without changing the broker's server-side query or exporting books.
        reconciliation = reconciliation_event_summary(raw)
        if reconciliation is not None:
            safe["reconciliation"] = reconciliation
            safe["reconciliation_code"] = reconciliation["code"]
            # Typed counters only; never retain the original Core detail
            # in a clipboard/selection row. Export rechecks every field.
            safe["reconciliation_export"] = exportable_reconciliation_aggregates(raw)
        # Only explicitly published Core references may link observations.
        # Never extract one from a timestamp, event ID or message text.
        reference = published_task_reference(raw)
        if reference is not None:
            safe["task_ref"] = reference
        connection = published_connection_attempt(raw)
        if connection is not None:
            safe.update(connection)
        # Full text search is explicitly local to this 50-item broker page.
        searchable = " ".join(
            safe[key] for key in ("timestamp", "source", "module", "severity", "message", "status")
        )
        if reconciliation is not None:
            searchable += " " + " ".join(reconciliation.values())
        if provider is not None:
            searchable += " " + provider
        if search and search.casefold() not in searchable.casefold():
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
    if type(source) is not str or source not in ("core_events", "app_log", "journal"):
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


# The broker's `provider_connect` projection is the ONLY source of these
# fields. Revalidate the complete Core schema and the physical log/source
# envelope: a generic PLUGIN row containing "operation_ref" proves nothing.
_CONNECTION_FIELDS = frozenset({
    "provider_id", "operation_ref", "phase", "status", "reason_code",
    "caller_category", "source_kind", "network_auth", "duration_ms",
})
_CONNECTION_CALLERS = frozenset({
    "startup/connect_configured", "opening_sync", "explicit_refresh",
    "profile_change", "plugin_activation", "other",
})
_CONNECTION_SOURCES = frozenset({"configured", "singleton_public", "preview", "virtual"})
_CONNECTION_PHASES = {
    "requested": ({"unknown"}, {"requested"}),
    "connect_invoked": ({"unknown"}, {"connect_invoked"}),
    "session_reused": ({"success"}, {"session_reused"}),
    "terminal": (
        {"success", "failed", "skipped", "cancelled"},
        {"connected", "connect_failed", "session_invalid", "plugin_missing",
         "plugin_not_ready", "interactive_auth_required", "cancelled"},
    ),
}
_CONNECTION_TERMINAL = {
    "success": {"connected"},
    "failed": {"connect_failed", "session_invalid"},
    "skipped": {"plugin_missing", "plugin_not_ready", "interactive_auth_required"},
    "cancelled": {"cancelled"},
}
_CONNECTION_PUBLIC_STATUS = {
    "unknown": "unknown", "success": "ok", "failed": "error",
    "skipped": "warning", "cancelled": "warning",
}


def _connection_envelope_valid(row: Mapping[str, Any], phase: str,
                               status: str, provider: str) -> bool:
    source, module = row.get("source"), row.get("module")
    if ((source, module) not in {("core_events", "PLUGIN"), ("app_log", "cifereca")}
            or row.get("provider_id") != provider or public_provider_id(provider) is None
            or row.get("message") != "connexion fournisseur · " + phase
            or row.get("status") != _CONNECTION_PUBLIC_STATUS.get(status)):
        return False
    return True


def published_connection_attempt(row: Mapping[str, Any]) -> dict[str, str] | None:
    """Read only an exact Core-attested connection observation.

    This is intentionally separate from task_ref: one connection request can
    have three phases and two physical channels, but proves zero HTTP auths.
    Never read a reference from generic row text, timestamps or event IDs.
    """
    if not isinstance(row, Mapping) or "operation_ref" in row:
        return None
    detail = row.get("detail")
    if type(detail) is not dict or set(detail) not in (
        _CONNECTION_FIELDS, _CONNECTION_FIELDS | {"scope_ref", "scope_kind"},
    ):
        return None
    phase, status, reason = (detail.get("phase"), detail.get("status"),
                             detail.get("reason_code"))
    if (type(phase) is not str or phase not in _CONNECTION_PHASES
            or type(status) is not str or type(reason) is not str
            or status not in _CONNECTION_PHASES[phase][0]
            or reason not in _CONNECTION_PHASES[phase][1]
            or (phase == "terminal" and reason not in _CONNECTION_TERMINAL[status])):
        return None
    operation = detail.get("operation_ref")
    provider = detail.get("provider_id")
    if (type(operation) is not str or re.fullmatch(r"[0-9a-f]{24}", operation) is None
            or type(provider) is not str
            or type(detail.get("caller_category")) is not str
            or detail["caller_category"] not in _CONNECTION_CALLERS
            or type(detail.get("source_kind")) is not str
            or detail["source_kind"] not in _CONNECTION_SOURCES
            or detail.get("network_auth") != "unknown"
            or type(detail.get("duration_ms")) is not int
            or not 0 <= detail["duration_ms"] <= 600000
            or not _connection_envelope_valid(row, phase, status, provider)):
        return None
    if "scope_ref" in detail and (
        type(detail["scope_ref"]) is not str
        or re.fullmatch(r"[0-9a-f]{24}", detail["scope_ref"]) is None
        or detail.get("scope_kind") != "connection"
    ):
        return None
    # Do not retain/export scope_ref, credential or anything beyond enums.
    return {
        "operation_ref": operation, "phase": phase,
        "caller_category": detail["caller_category"],
        "source_kind": detail["source_kind"],
        # Internal anchor from the original validated detail. A later rewrite
        # of the display provider cannot carry this attestation with it.
        "connection_provider_id": provider,
        "network_auth": "unknown", "connection_status": status,
    }


def published_operation_reference(row: Mapping[str, Any]) -> str | None:
    """Validate the normalized, closed UI observation before grouping/export.

    It never trusts an arbitrary operation_ref alone; sanitized_rows is the
    only producer of these exact companion fields from a Core broker event.
    """
    if not isinstance(row, Mapping):
        return None
    ref, phase, status = (row.get("operation_ref"), row.get("phase"),
                          row.get("connection_status"))
    if (type(ref) is not str or re.fullmatch(r"[0-9a-f]{24}", ref) is None
            or type(phase) is not str or phase not in _CONNECTION_PHASES
            or type(status) is not str or status not in _CONNECTION_PHASES[phase][0]
            or type(row.get("caller_category")) is not str
            or row["caller_category"] not in _CONNECTION_CALLERS
            or type(row.get("source_kind")) is not str
            or row["source_kind"] not in _CONNECTION_SOURCES
            or row.get("network_auth") != "unknown"
            or type(row.get("connection_provider_id")) is not str
            or row["connection_provider_id"] != row.get("provider_id")
            or not _connection_envelope_valid(row, phase, status, row["connection_provider_id"])):
        return None
    return ref


def connection_provenance_label(row: Mapping[str, Any]) -> str:
    """Short GTK-only caption from a Core-validated connection request.

    A bare hexadecimal-looking reference is never enough to reach the view.
    """
    reference = published_operation_reference(row)
    if reference is None:
        return ""
    return (
        f" · Demande Core : {reference}"
        f" · Phase : {row['phase']}"
        f" · Déclencheur : {row['caller_category']}"
    )


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
        operation = published_operation_reference(row)
        task = published_task_reference({
            "source": row.get("source"), "task_ref": row.get("task_ref"),
        })
        # Namespaces prevent a task and a connect operation from colliding.
        # A connection request is not a worker task or a network invocation.
        key = ("connection", operation) if operation else (
            ("task", task) if task else None
        )
        group = by_reference.get(key) if key is not None else None
        if group is None:
            group = {
                "task_ref": task if operation is None else None,
                "operation_ref": operation, "events": [],
                "label": (f"Connexion {operation} · demande Core attestée"
                          if operation else
                          f"Tâche {task} · référence Core publiée" if task
                          else "Corrélation non disponible"),
            }
            groups.append(group)
            if key is not None:
                by_reference[key] = group
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
    "journal": "Journal local unifié",
    "core_events": "Événements Core (mémoire)",
    "app_log": "Journal applicatif (lecture bornée)",
    "inconnue": "Canal non identifié",
}
_CHANNEL_REASON_LABELS = {
    "rotation_or_read_gap": "Une ou plusieurs rotations sont illisibles ou incomplètes",
    "active_log_missing": "Fichier principal absent ; anciens fragments seulement",
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
            return min(1000000, max(0, value))
        if type(value) is str and len(value) <= 6 and value.isdecimal():
            return min(1000000, int(value))
        return 0

    total = bounded_count(payload.get("total"))
    offset = bounded_count(payload.get("offset"))
    shown = len(rows)
    if not payload.get("sources"):
        return "Canaux de diagnostic indisponibles — aucune donnée exploitable"
    suffix = " · recherche dans cette page uniquement" if search else ""
    return f"{shown} événements affichés · {total} disponibles · page à partir de {offset + 1}{suffix}" + cover_failure_page_summary(rows)


def empty_observation(payload: Mapping[str, Any], *, period: str,
                      severity: str = "", module: str = "", search: str = "",
                      domain: str = "") -> str:
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
    if severity or module or search or domain:
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
    if current_offset < following <= min(1000000, current_offset + PAGE_SIZE) and following < total:
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
        provider = public_provider_id(row.get("provider_id"))
        if provider is not None:
            item["provider_id"] = provider
        reference = published_task_reference({
            "source": row.get("source"), "task_ref": row.get("task_ref"),
        })
        if reference is not None:
            item["task_ref"] = reference
        if published_operation_reference(row) is not None:
            for field in (
                "operation_ref", "phase", "caller_category", "source_kind",
                "network_auth", "connection_status",
            ):
                item[field] = row[field]
        aggregate = sanitized_reconciliation_export(row)
        if aggregate is not None:
            item["reconciliation"] = aggregate
        selected.append(item)
    if len(selected) > max_events:
        raise ValueError("too many selected events for one export")
    result = json.dumps({
        "notice": "Événements Core : corrélation par task_ref ou operation_ref "
                  "attestées ; authentifications réseau inconnues. Étapes potentiellement "
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
