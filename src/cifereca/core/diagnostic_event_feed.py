"""Bounded, read-only public Diagnostics event feed.

Never expose raw application logs, filesystem paths, credentials or arbitrary
runtime output through the plugin broker. Local log files are only opened from
the Core-owned fixed log location. There is no network or subprocess access.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import os
from pathlib import Path
import re
import stat
from typing import Any

from .diagnostic_numilog_projection import (
    public_numilog_fields, public_numilog_message, public_numilog_session_log,
    public_numilog_session_fields,
)
from .diagnostic_kobo_projection import (
    public_kobo_backup_decision, public_kobo_backup_log,
    public_kobo_backup_message,
)
from .numilog_startup_diagnostics import public_log_fields as public_numilog_startup_log
from .public_provider_diagnostics import (
    canonical_public_provider_id,
    public_connection_fields,
    public_connection_log_fields,
    public_connection_success_fields,
    public_connection_success_log_fields,
)
from .diagnostic_provider_connection_attempt import (
    public_fields as public_connect_attempt_fields,
    log_fields as public_connect_attempt_log,
)
from .diagnostic_loan_download import (
    public_fields as public_loan_download_fields,
    public_log_fields as public_loan_download_log,
)
from .diagnostic_bnf_loan_action import (
    public_fields as public_bnf_action_fields,
    public_log_fields as public_bnf_action_log,
    public_message as public_bnf_action_message,
)
from .diagnostic_source_sync import (
    public_fields as public_source_sync_fields,
    public_log_fields as public_source_sync_log,
    public_message as public_source_sync_message,
)
from .diagnostic_adept import (
    public_fields as public_adept_fields, public_log_fields as public_adept_log,
    public_message as public_adept_message,
)
from ..diagnostics import Diagnostics
from .diagnostic_cover_failure import (
    public_fields as public_cover_failure_fields,
    public_message as public_cover_failure_message,
)
from .diagnostic_reconciliation import (
    known_reason_code, public_fields as public_reconciliation_fields,
    public_reconciliation_log_fields,
)

from .diagnostic_log_line import _LOG_LINE

_TASK_CORRELATION = re.compile(
    r"TRACE task correlation task_ref=([0-9a-f]{12}) status=(success|failed|cancelled)"
)
_TRACE = re.compile(r"^TRACE ([a-zA-Z][\w-]{0,30}) ([a-zA-Z][\w-]{0,45})(?:\s|$)")
_SAFE_FIELDS = re.compile(
    r"\b(phase|status|outcome|operation|duration_ms|count|returncode)="
    r"([a-zA-Z][a-zA-Z_-]{0,32}|\d{1,6})\b"
)
_ALLOWED_FIELDS = {"phase", "status", "outcome", "operation", "duration_ms", "count", "returncode"}
_SAFE_TRACE_VALUES = frozenset({
    "ok", "success", "failed", "error", "warning", "partial",
    "available", "unavailable", "disabled", "empty", "pending",
    "running", "completed", "cancelled", "skipped", "timeout",
    "accepted", "rejected", "attention_required",
    "adobe_return_confirmed",
})


def _safe_trace_field(key: str, value: str) -> bool:
    if key not in _ALLOWED_FIELDS:
        return False
    if key in {"duration_ms", "count", "returncode"}:
        return value.isascii() and value.isdecimal()
    return value.lower() in _SAFE_TRACE_VALUES


_MAX_BYTES_PER_LOG = 32768
_MAX_EVENTS_PER_FILE = 200
_MAX_RESULTS = 100
_STRUCTURED_TERMS = frozenset({
    "sync", "source", "opening", "connect", "download", "acquire", "acquisition",
    "return", "verification", "retry", "import", "runtime", "plugin", "parse",
    "catalog", "search", "library", "update", "install", "check", "health",
    "error", "warning", "state", "fetch", "adept", "pnb", "paris", "kobo",
})
_CATEGORIES = frozenset({
    "CORE", "SYNC", "ADEPT", "PNB", "PARIS", "KOBO", "PLUGIN",
    "UPDATER", "UI", "CATALOG", "DIAGNOSTICS", "IMPORT", "LIBRARY", "NUMILOG", "LOAN", "COVER",
})


def _stamp(value: str) -> datetime:
    instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if instant.tzinfo is None:
        raise ValueError("timezone required for diagnostic date filters")
    return instant.astimezone(timezone.utc)


def _event_id(source: str, timestamp: str, detail: str) -> str:
    return sha256((source + "\0" + timestamp + "\0" + detail).encode()).hexdigest()[:16]


def _log_lines(
    path: Path,
) -> tuple[list[tuple[int, str]], str, tuple[int, int] | None]:
    """Read a 32 KiB tail with absolute byte positions and file identity.

    (device, inode, byte offset) survives a rename to cifereca.log.1 on
    POSIX filesystems. It is never exposed outside the hashed event_id.
    """
    # The local log may have been relocated through a symlinked directory,
    # not only a symlinked leaf. Read only through no-follow directory FDs;
    # without the platform guard, publish no log-derived events at all.
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        return [], "unavailable", None
    directory_fd = -1
    try:
        absolute = Path(os.path.abspath(path))
        flags_dir = (
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            | getattr(os, "O_CLOEXEC", 0)
        )
        directory_fd = os.open("/", flags_dir)
        for part in absolute.parts[1:-1]:
            next_fd = os.open(part, flags_dir, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = next_fd
        flags = (
            os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0)
            | getattr(os, "O_CLOEXEC", 0)
        )
        fd = os.open(absolute.name, flags, dir_fd=directory_fd)
        with os.fdopen(fd, "rb") as handle:
            identity = os.fstat(handle.fileno())
            if not stat.S_ISREG(identity.st_mode):
                return [], "unavailable", None
            start = max(0, identity.st_size - _MAX_BYTES_PER_LOG)
            handle.seek(start)
            data = handle.read(_MAX_BYTES_PER_LOG)
            lines: list[tuple[int, str]] = []
            offset = start
            # Split bytes before decoding: Unicode replacement characters must
            # not change the original absolute offsets, even after truncation.
            for raw_line in data.splitlines(keepends=True):
                lines.append((
                    offset, raw_line.decode("utf-8", errors="replace").rstrip("\r\n"),
                ))
                offset += len(raw_line)
            if start and lines:
                lines.pop(0)  # the first record may start mid-line
            status = (
                "partial" if start or len(lines) > _MAX_EVENTS_PER_FILE
                else "available"
            )
            return lines[-_MAX_EVENTS_PER_FILE:], status, (
                identity.st_dev, identity.st_ino,
            )
    except FileNotFoundError:
        # Missing rotation slots are normal; all other I/O failures mean
        # the source exists but its evidence cannot be trusted as complete.
        return [], "missing", None
    except (OSError, ValueError):
        return [], "unavailable", None
    finally:
        if directory_fd >= 0:
            os.close(directory_fd)


def _latest_published_event(events: list[dict[str, Any]]) -> str | None:
    """Newest public event in Core's bounded source window, not file mtime."""
    if not events:
        return None
    return max(_stamp(event["timestamp"]) for event in events).isoformat()


def _source_logs(log_path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    events: list[dict[str, Any]] = []
    observed = False
    partial = False
    unreadable = False
    active_missing = False
    for index in (3, 2, 1, 0):
        path = Path(str(log_path) + (f".{index}" if index else ""))
        lines, status, identity = _log_lines(path)
        if status == "missing":
            if index == 0:
                active_missing = True
            continue
        if status == "unavailable" or identity is None:
            # An existing but unreadable/nonregular rotation must never be
            # silently treated as an absent, harmless rotation slot.
            unreadable = True
            continue
        observed = True
        partial |= status == "partial"
        for byte_offset, line in lines:
            match = _LOG_LINE.match(line)
            if not match or not (
                match["logger"] in {"cifereca", "cifereca_numilog"}
                or match["logger"].startswith("cifereca.")
            ):
                continue
            # During migration, DIAG_JSON belongs exclusively to the
            # canonical physical journal; the legacy dual-channel facade
            # must not count this additional emission a second time.
            if match["body"].startswith("DIAG_JSON "):
                continue
            # Raw log messages may contain arbitrary PII or ACSM/licence URLs.
            # Export only known machine-generated TRACE categories and small
            # allowlisted status fields. Everything else remains private.
            trace = _TRACE.match(match["body"])
            task_correlation = (
                _TASK_CORRELATION.fullmatch(match["body"])
                if match["logger"] == "cifereca" else None
            )
            # This exact Core-owned Kobo decision is a small typed fact,
            # not a general-purpose log parser or an inferred task link.
            kobo_detail = (
                public_kobo_backup_log(match["body"])
                if match["logger"] == "cifereca" else {}
            )
            numilog_detail = (
                public_numilog_startup_log(match["body"])
                if match["logger"] == "cifereca" else {}
            )
            numilog_session_detail = (
                public_numilog_session_fields(match["body"])
                if match["logger"] == "cifereca_numilog" else {}
            )
            source_sync_detail = (
                public_source_sync_log(match["body"])
                if match["logger"] == "cifereca" else {}
            )
            loan_download_detail = (
                public_loan_download_log(match["body"])
                if match["logger"] == "cifereca" else {}
            )
            bnf_action_detail = (
                public_bnf_action_log(match["body"])
                if match["logger"] == "cifereca" else {}
            )
            adept_detail = (
                public_adept_log(match["body"]) if match["logger"] == "cifereca" else {}
            )
            connection_detail = (
                public_connection_log_fields(match["body"])
                if match["logger"] == "cifereca" else {}
            )
            connection_success_detail = (
                public_connection_success_log_fields(match["body"])
                if match["logger"] == "cifereca" else {}
            )
            connect_attempt_detail = (
                public_connect_attempt_log(match["body"])
                if match["logger"] == "cifereca" else {}
            )
            audit_detail = (
                public_reconciliation_log_fields(match["body"])
                if match["logger"] == "cifereca" else {}
            )
            audit_level = {
                "complete": "INFO", "partial": "WARNING",
                "cancelled": "WARNING", "failed": "ERROR",
            }
            if audit_detail and match["level"] != audit_level[audit_detail["status"]]:
                audit_detail = {}
            if connect_attempt_detail:
                message = "connexion fournisseur · " + connect_attempt_detail["phase"]
            elif bnf_action_detail:
                message = public_bnf_action_message(bnf_action_detail)
            elif loan_download_detail:
                message = "Téléchargement du prêt · " + loan_download_detail["status"] + " · " + loan_download_detail["code"]
            elif audit_detail:
                message = {
                    "complete": "réconciliation locale terminée",
                    "partial": "réconciliation locale incomplète",
                    "failed": "réconciliation locale indisponible",
                    "cancelled": "réconciliation locale interrompue",
                }[audit_detail["status"]]
            elif connection_success_detail:
                message = "connexion fournisseur réussie"
            elif connection_detail:
                message = "connexion fournisseur échouée"
            elif adept_detail:
                message = public_adept_message(adept_detail)
            elif source_sync_detail:
                message = public_source_sync_message(source_sync_detail)
            elif numilog_detail:
                message = public_numilog_message("startup_materialization", numilog_detail)
            elif kobo_detail:
                message = public_kobo_backup_message(kobo_detail)
            elif match["logger"] == "cifereca_numilog":
                message = public_numilog_session_log(match["body"]) or (
                    "[contenu libre masqué : consulter le journal local]"
                )
            elif task_correlation:
                message = task_correlation[0]
            elif trace and trace.group(1).lower() in _STRUCTURED_TERMS and trace.group(2).lower() in (_STRUCTURED_TERMS | {"core"}):
                message = "TRACE " + trace.group(1).lower() + " " + trace.group(2).lower()
                suffix = match["body"][trace.end():]
                pairs = [
                    (key, value) for key, value in _SAFE_FIELDS.findall(suffix)
                    if _safe_trace_field(key, value)
                ][:4]
                if pairs:
                    message += " " + " ".join(k + "=" + v for k, v in pairs)
            else:
                message = "[contenu libre masqué : consulter le journal local]"
            try:
                ts = datetime.strptime(match["date"], "%Y-%m-%d %H:%M:%S,%f")
                # Legacy file timestamps are local wall time; interpret once
                # in the host timezone, then publish the instant in UTC.
                ts = ts.astimezone().astimezone(timezone.utc).isoformat()
            except ValueError:
                continue
            # Logger names are not guaranteed to be static: extensions may
            # embed account identifiers. Publish only recognised components.
            logger_parts = match["logger"].split(".")
            safe_parts = [
                "cifereca_numilog" if match["logger"] == "cifereca_numilog" else "cifereca"
            ]
            for part in logger_parts[1:]:
                if part.lower() not in (_STRUCTURED_TERMS | {"core", "diagnostics", "ui", "service", "worker"}):
                    break
                safe_parts.append(part.lower())
            # Keep the physical logger in "module". A *separate*, fixed
            # business domain is supported only for a fully validated TRACE
            # from the exact known producer. Never classify arbitrary TRACE
            # text, guessed module names or timestamps as domain evidence.
            business_module = (
                "KOBO" if kobo_detail else
                "NUMILOG" if numilog_detail or numilog_session_detail else
                "SYNC" if source_sync_detail else
                "PNB" if loan_download_detail and loan_download_detail["provider_id"] == "paris" else
                "LOAN" if loan_download_detail or bnf_action_detail else
                "ADEPT" if adept_detail else
                "PLUGIN" if connection_detail or connection_success_detail or connect_attempt_detail else
                "LIBRARY" if audit_detail else None
            )
            # A provider identity comes only from a closed Core log schema,
            # never from a logger suffix, URL, TRACE free text or account label.
            # ADEPT is a shared runtime; a Numilog plugin session line alone
            # has no Core registry attestation. Neither proves a provider.
            attested_provider_id = (
                connect_attempt_detail.get("provider_id")
                or connection_detail.get("provider_id")
                or connection_success_detail.get("provider_id")
                or source_sync_detail.get("provider_id")
                or loan_download_detail.get("provider_id")
                or bnf_action_detail.get("provider_id")
                or ("kobo" if kobo_detail else None)
                or ("numilog" if numilog_detail else None)
            )
            events.append({
                "timestamp": ts,
                "source": "app_log",
                **({"provider_id": attested_provider_id} if attested_provider_id else {}),
                "module": ".".join(safe_parts)[:90],
                **({"business_module": business_module} if business_module else {}),
                "severity": match["level"].lower(),
                "message": message[:500] if numilog_detail or source_sync_detail or loan_download_detail or adept_detail else message[:240],
                **({"detail": kobo_detail} if kobo_detail else {}),
                **({"detail": connection_detail, "status": "error"} if connection_detail else {}),
                **({"detail": connection_success_detail, "status": "ok"}
                   if connection_success_detail else {}),
                **({"detail": connect_attempt_detail, "status": (
                    "error" if connect_attempt_detail["status"] == "failed" else
                    "warning" if connect_attempt_detail["status"] in {"skipped", "cancelled"} else
                    "ok" if connect_attempt_detail["status"] == "success" else "unknown"
                )} if connect_attempt_detail else {}),
                **({"detail": audit_detail, "status": audit_detail["status"]}
                   if audit_detail else {}),
                **({"task_ref": audit_detail["task_ref"]}
                   if "task_ref" in audit_detail else {}),
                **({"detail": numilog_detail} if numilog_detail else {}),
                **({"detail": numilog_session_detail} if numilog_session_detail else {}),
                **({"detail": source_sync_detail, "status": source_sync_detail["status"]}
                   if source_sync_detail else {}),
                **({"detail": loan_download_detail, "status": loan_download_detail["status"]}
                   if loan_download_detail else {}),
                **({"task_ref": loan_download_detail["task_ref"]}
                   if "task_ref" in loan_download_detail else {}),
                **({"detail": bnf_action_detail, "status": bnf_action_detail["status"],
                    **({"task_ref": bnf_action_detail["task_ref"]}
                       if "task_ref" in bnf_action_detail else {})}
                   if bnf_action_detail else {}),
                **({"detail": adept_detail, "status": adept_detail["status"]} if adept_detail else {}),
                **({"task_ref": task_correlation[1]} if task_correlation else {}),
                # Neither the rotation suffix nor a moving tail line index
                # participates in the ID. Distinct lines remain distinct.
                # A file can be overwritten on the same inode/offset.
                # Include every published event discriminator so a change of
                # severity or sanitized module cannot reuse an old reference.
                "event_id": _event_id(
                    "app_log", ts,
                    f"{identity[0]}:{identity[1]}:{byte_offset}:"
                    f"{match['level']}:{'.'.join(safe_parts)}:{message}",
                ),
            })
    # A missing active file with only archived records, or an unreadable
    # rotation next to a readable one, cannot establish full coverage.
    incomplete = observed and (unreadable or active_missing)
    partial = partial or incomplete
    status = "partial" if partial else ("available" if observed else "unavailable")
    state = {
        "source": "app_log",
        "status": "partial" if partial else (
            status if events else ("empty" if observed else "unavailable")
        ),
        "reason": "log_source_incomplete" if incomplete else (
            "log_tail_bounded" if partial else (
                "no_matching_events" if observed and not events else ""
            )
        ),
    }
    if events:
        state["last_event"] = _latest_published_event(events)
    return events, state


def _source_diagnostics(diagnostics: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not getattr(diagnostics, "enabled", True):
        return [], {"source": "core_events", "status": "disabled", "reason": "diagnostics_disabled"}
    try:
        snapshot = diagnostics.snapshot()
        if not isinstance(snapshot, (list, tuple)):
            raise ValueError("snapshot must be a sequence")
        rows = snapshot[-500:]
        incomplete = len(snapshot) > len(rows)
    except Exception:
        # Snapshot providers can fail with I/O/runtime exceptions as well as
        # malformed values. Keep the other public log source consultable and
        # expose only the fixed code, never the exception text or path.
        return [], {"source": "core_events", "status": "unavailable", "reason": "snapshot_unavailable"}
    events: list[dict[str, Any]] = []
    used_ids: set[str] = set()
    duplicate_offsets: dict[str, int] = {}
    for row in rows:
        if not isinstance(row, dict):
            incomplete = True
            continue
        stamp = row.get("timestamp")
        if not isinstance(stamp, str):
            incomplete = True
            continue
        try:
            utc_stamp = _stamp(stamp).isoformat()
        except (ValueError, TypeError):
            incomplete = True
            continue
        raw_category = row.get("category")
        raw_operation = row.get("operation")
        raw_status = row.get("status")
        category = raw_category.upper() if isinstance(raw_category, str) else ""
        module = category if category in _CATEGORIES else "DIAGNOSTICS"
        operation = raw_operation if isinstance(raw_operation, str) else ""
        status = raw_status.lower() if isinstance(raw_status, str) else ""
        severity = "error" if status in {"error", "failed"} else "warning" if status in {
            "warning", "attention_required", "partial"
        } else "info"
        # Even structured Diagnostics text can include names/paths. Publish
        # only a closed vocabulary of operational terms, never raw free text.
        terms = [word for word in re.findall(r"[a-zA-Z]+", operation.lower())
                 if word in _STRUCTURED_TERMS][:5]
        message = " ".join(terms) if terms else "[opération masquée]"
        public_detail = {}
        # Only a validated Core producer may assign this closed domain.
        # Generic PLUGIN/LIBRARY messages must not become provenance.
        business_domain = None
        # Only the dedicated Core cover worker can attest a failure reason.
        # Legacy generic COVER/download emissions carry no provenance marker;
        # they remain readable but cannot acquire a typed failure label.
        if (category == "COVER" and operation == "download" and status == "error"
                and type(diagnostics) is Diagnostics
                and row.get("verified_cover_failure") is True):
            raw_detail = row.get("detail")
            candidate = public_cover_failure_fields({
                "code": (raw_detail["code"] if type(raw_detail) is dict
                         and set(raw_detail) == {"code"} else None),
                "duration_ms": row.get("duration_ms"),
            })
            if candidate:
                public_detail = {"code": candidate["code"]}
                message = public_cover_failure_message(candidate)
        # The Core attests user-requested Browser Recorder provisioning
        # operations. A generic PLUGIN.add() record with identical text
        # cannot publish even a fixed code without this provenance flag.
        if (category == "PLUGIN"
                and row.get("verified_browser_recorder") is True
                and type(diagnostics) is Diagnostics):
            from .browser_recorder_diagnostics import verified_fields
            attested = verified_fields(row.get("detail"), operation, status)
            if attested:
                public_detail = attested
                message = operation
                business_domain = "browser_recorder"
        # This projection is restricted to the Core connection producer's
        # exact fixed operation/code pair. Other PLUGIN records remain masked,
        # even if an arbitrary plugin supplies a well-formed provider_id.
        if (category == "PLUGIN"
                and operation in {"provider connect", "public provider connect"}
                and status in {"error", "failed"}
                and type(diagnostics) is Diagnostics):
            validated = public_connection_fields(row.get("detail"))
            if (validated and row.get("verified_public_provider_id")
                    == validated["provider_id"]):
                public_detail = validated
                message = "connexion fournisseur échouée"
        if (category == "PLUGIN" and operation == "provider_connect"
                and type(diagnostics) is Diagnostics
                and row.get("verified_provider_connection_attempt") is True):
            candidate = public_connect_attempt_fields(row.get("detail"))
            expected_status = (
                "error" if candidate.get("status") == "failed" else
                "warning" if candidate.get("status") in {"skipped", "cancelled"} else
                "ok" if candidate.get("status") == "success" else "unknown"
            )
            if candidate and status == expected_status:
                public_detail = candidate
                message = "connexion fournisseur · " + candidate["phase"]
        # Success is independently attested and schema-checked. A generic
        # plugin claiming "provider connected" cannot become Core evidence.
        if (category == "PLUGIN"
                and operation in {"provider connected", "public provider connected"}
                and status == "ok"
                and row.get("verified_public_provider_success") is True
                and type(diagnostics) is Diagnostics):
            validated = public_connection_success_fields(row.get("detail"))
            if (validated and validated["source_kind"] == (
                "singleton_public" if operation == "public provider connected"
                else "configured"
            )):
                public_detail = validated
                message = "connexion fournisseur réussie"
        if (category == "KOBO"
                and operation == "automatic backup decision"
                and type(diagnostics) is Diagnostics
                and row.get("verified_kobo_backup") is True):
            public_detail = public_kobo_backup_decision(row.get("detail"))
            message = public_kobo_backup_message(public_detail) or "[opération masquée]"
        reconciliation_detail = {}
        if (category == "LIBRARY"
                and operation == "library_reconciliation_audit"
                and type(diagnostics) is Diagnostics
                and row.get("verified_reconciliation") is True):
            inspected = public_reconciliation_fields(row.get("detail"))
            if inspected and status == (
                    "ok" if inspected["status"] == "complete" else inspected["status"]):
                reconciliation_detail = inspected
                public_detail = inspected
                business_domain = "reconciliation"
                message = {
                    "complete": "réconciliation locale terminée",
                    "partial": "réconciliation locale incomplète",
                    "failed": "réconciliation locale indisponible",
                    "cancelled": "réconciliation locale interrompue",
                }[inspected["status"]]
                severity = ("info" if inspected["status"] == "complete"
                            else "error" if inspected["status"] == "failed"
                            else "warning")
        source_sync_detail = {}
        loan_download_detail = {}
        bnf_action_detail = {}
        adept_detail = {}
        if (category == "ADEPT" and operation == "kobo adept"
                and type(diagnostics) is Diagnostics and row.get("verified_kobo_adept") is True):
            adept_detail = public_adept_fields(row.get("detail"))
            if adept_detail:
                public_detail = adept_detail
                message = public_adept_message(adept_detail)
                severity = "error" if adept_detail["status"] == "failed" else "info"
        if (category == "SYNC" and operation == "source sync"
                and type(diagnostics) is Diagnostics
                and row.get("verified_source_sync") is True):
            source_sync_detail = public_source_sync_fields(row.get("detail"))
            if source_sync_detail:
                public_detail = source_sync_detail
                message = public_source_sync_message(public_detail)
                severity = ("error" if public_detail["status"] == "failed" else
                            "warning" if public_detail["status"] == "cancelled" else "info")
        if (category in {"PNB", "LOAN"} and operation == "download_loan"
                and type(diagnostics) is Diagnostics
                and row.get("verified_loan_download") is True):
            candidate = public_loan_download_fields(row.get("detail"))
            if (candidate and category == (
                "PNB" if candidate["provider_id"] == "paris" else "LOAN"
            ) and status == (
                "ok" if candidate["status"] == "success" else candidate["status"]
            )):
                loan_download_detail = candidate
                public_detail = candidate
                # Only attested Core terminal download operations may enter this domain.
                business_domain = "loan_download"
                message = "Téléchargement du prêt · " + candidate["status"] + " · " + candidate["code"]
                severity = ("info" if candidate["status"] == "success"
                            else "error" if candidate["status"] in {"failed", "refused"}
                            else "warning")
        if (category == 'LOAN' and operation == 'bnf_loan_action'
                and type(diagnostics) is Diagnostics
                and row.get('verified_bnf_loan_action') is True):
            candidate = public_bnf_action_fields(row.get('detail'))
            if candidate and status == ('ok' if candidate['status']=='success' else candidate['status']):
                bnf_action_detail = candidate
                public_detail = candidate
                business_domain = 'loan_download'
                message = public_bnf_action_message(candidate)
                severity = ('info' if candidate['status']=='success' else
                            'error' if candidate['status']=='failed' else 'warning')
        if category == "NUMILOG":
            # Startup facts must originate from the dedicated Core emitter.
            # A generic plugin/diagnostics.add with a correctly shaped
            # dictionary proves no source, phase, result or task provenance.
            verified_startup = (
                operation != "startup_materialization" or (
                    type(diagnostics) is Diagnostics
                    and row.get("verified_numilog_startup") is True
                )
            )
            if verified_startup:
                public_detail = public_numilog_fields(
                    operation, row.get("detail"), row.get("duration_ms"),
                )
                task_ref = row.get("task_ref")
                if (operation in {"acquisition_delivery", "session_state", "startup_materialization"}
                        and type(task_ref) is str and re.fullmatch(r"[0-9a-f]{12}", task_ref)):
                    public_detail["task_ref"] = task_ref
                message = public_numilog_message(operation, public_detail) or "[opération masquée]"
            else:
                message = "[opération masquée]"
        # Do not infer the provider from a business category (PNB/PLUGIN).
        # Only typed observations with Core provenance attest this identity.
        # runtime.kobo-adept also serves Paris, Numilog and manual imports.
        attested_provider_id = None
        if bnf_action_detail:
            attested_provider_id = 'bnf-pret'
        elif loan_download_detail:
            attested_provider_id = loan_download_detail["provider_id"]
        elif source_sync_detail:
            attested_provider_id = source_sync_detail["provider_id"]
        elif (category == "PLUGIN"
              and operation in {"provider connect", "public provider connect"}
              and row.get("verified_public_provider_id") == public_detail.get("provider_id")
              and public_detail.get("code")):
            attested_provider_id = public_detail["provider_id"]
        elif (category == "PLUGIN" and operation == "provider_connect"
              and row.get("verified_provider_connection_attempt") is True
              and public_detail.get("operation_ref")):
            attested_provider_id = public_detail["provider_id"]
        elif (category == "PLUGIN"
              and operation in {"provider connected", "public provider connected"}
              and row.get("verified_public_provider_success") is True
              and public_detail.get("code") == "provider_connected"):
            attested_provider_id = public_detail["provider_id"]
        elif (category == "KOBO"
                             and operation == "automatic backup decision"
                             and row.get("verified_kobo_backup") is True
                             and public_detail):
            attested_provider_id = "kobo"
        elif (category == "NUMILOG" and operation == "startup_materialization"
              and row.get("verified_numilog_startup") is True and public_detail):
            attested_provider_id = "numilog"
        base_id = _event_id("core_events", utc_stamp, category + ":" + operation)
        event_id = base_id
        # Two records may share the same timestamp/category/operation.
        # Preserve the historical hash of a unique record, but avoid false
        # reference provenance when two published records collide.
        duplicate_number = duplicate_offsets.get(base_id, 0)
        while event_id in used_ids:
            duplicate_number += 1
            event_id = _event_id(
                "core_events", utc_stamp,
                f"{category}:{operation}:duplicate:{duplicate_number}",
            )
        used_ids.add(event_id)
        duplicate_offsets[base_id] = duplicate_number
        events.append({
            "timestamp": utc_stamp,
            "source": "core_events",
            "module": module,
            **({"domain": business_domain} if business_domain else {}),
            **({"provider_id": attested_provider_id} if attested_provider_id else {}),
            "severity": severity,
            "message": message,
            **({"detail": public_detail} if public_detail else {}),
            **({"task_ref": bnf_action_detail['task_ref']}
               if 'task_ref' in bnf_action_detail else {}),
            **({"task_ref": loan_download_detail["task_ref"]}
               if "task_ref" in loan_download_detail else {}),
            **({"task_ref": reconciliation_detail["task_ref"]}
               if "task_ref" in reconciliation_detail else {}),
            "status": bnf_action_detail['status'] if bnf_action_detail else loan_download_detail["status"] if loan_download_detail else reconciliation_detail["status"] if reconciliation_detail else adept_detail["status"] if adept_detail else source_sync_detail["status"] if source_sync_detail else (
                status if status in {"ok", "error", "failed", "warning",
                                         "attention_required", "partial", "success"}
                else "unknown"),
            "event_id": event_id,
        })
    state = {
        "source": "core_events",
        "status": "partial" if incomplete else ("available" if events else "empty"),
        "reason": "snapshot_records_incomplete" if incomplete else (
            "" if events else "no_recorded_events"
        ),
    }
    if events:
        state["last_event"] = _latest_published_event(events)
    return events, state


def query_diagnostic_events(
    diagnostics: Any, *, log_path: Path,
    source: str = "all", module: str = "", severity: str = "",
    provider_id: str = "", domain: str = "",
    since: str | None = None, until: str | None = None,
    limit: int = 50, offset: int = 0, reason_code: str = "",
) -> dict[str, Any]:
    """A bounded/paginated snapshot with explicit source states.

    Log content is intentionally *not* an unredacted viewer. The caller may
    only select sources and filters, never a filesystem path.
    """
    if not isinstance(source, str) or source not in {"all", "core_events", "app_log"}:
        raise ValueError("unknown diagnostic source")
    if not isinstance(module, str) or len(module) > 90:
        raise ValueError("diagnostic module must be a string of at most 90 characters")
    if type(domain) is not str or domain not in (
        "", "browser_recorder", "reconciliation", "loan_download",
    ):
        raise ValueError("unknown diagnostic domain")
    # Provider and module are independent filters. A caller-supplied value
    # is never interpreted as a provider name, account alias or URL.
    if type(provider_id) is not str or (
        provider_id and canonical_public_provider_id(provider_id) is None
    ):
        raise ValueError("invalid canonical provider id")
    if type(reason_code) is not str or (
        reason_code and not known_reason_code(reason_code)
    ):
        raise ValueError("unknown reconciliation reason code")
    if not isinstance(severity, str) or severity.lower() not in {"", "debug", "info", "warning", "error", "critical"}:
        raise ValueError("unknown diagnostic severity")
    if any(value is not None and (not isinstance(value, str) or len(value) > 64) for value in (since, until)):
        raise ValueError("diagnostic date filters must be bounded ISO-8601 strings")
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= _MAX_RESULTS:
        raise ValueError("diagnostic limit must be 1..100")
    if not isinstance(offset, int) or isinstance(offset, bool) or not 0 <= offset <= 10000:
        raise ValueError("invalid diagnostic offset")
    lower = _stamp(since) if since is not None else None
    upper = _stamp(until) if until is not None else None
    if lower and upper and lower > upper:
        raise ValueError("diagnostic date range reversed")
    events: list[dict[str, Any]] = []
    states: list[dict[str, Any]] = []
    if source in {"all", "core_events"}:
        rows, state = _source_diagnostics(diagnostics)
        events.extend(rows)
        states.append(state)
    if source in {"all", "app_log"}:
        rows, state = _source_logs(log_path)
        events.extend(rows)
        states.append(state)
    if module:
        # "module" is the logger on app_log, but the Core producer category on
        # core_events. A KOBO/NUMILOG business filter must not discard a
        # canonical app_log observation solely because its logger is cifereca.
        # Only the exact closed schema parser above may set business_module.
        prefix = module.casefold()[:90]
        events = [
            row for row in events
            if row["module"].casefold().startswith(prefix)
            or (type(row.get("business_module")) is str
                and row["business_module"].casefold().startswith(prefix))
        ]
    if provider_id:
        events = [row for row in events if row.get("provider_id") == provider_id]
    if domain:
        # Strict Core-attested classification, applied *before* pagination.
        # Never search private detail, logger text or plugin-supplied prose.
        events = [
            row for row in events
            if row["source"] == "core_events" and row.get("domain") == domain
        ]
    if severity:
        if severity.lower() not in {"debug", "info", "warning", "error", "critical"}:
            raise ValueError("unknown diagnostic severity")
        events = [row for row in events if row["severity"] == severity.lower()]
    if lower or upper:
        events = [
            row for row in events
            if (not lower or _stamp(row["timestamp"]) >= lower)
            and (not upper or _stamp(row["timestamp"]) <= upper)
        ]
    if reason_code:
        # Apply before sorting/pagination to preserve a correct global total.
        # Never inspect free-form log messages or fabricated plugin fields:
        # only Core-attested LIBRARY terminal projections contain reasons.
        events = [
            row for row in events
            if row.get("source") == "core_events"
            and row.get("module") == "LIBRARY"
            and type(row.get("detail")) is dict
            and row["detail"].get("operation") == "library_reconciliation_audit"
            and reason_code in row["detail"].get("reasons", ())
        ]
    events.sort(key=lambda row: (_stamp(row["timestamp"]), row["source"], row["event_id"]))
    total = len(events)
    page = events[offset:offset + limit]
    # A healthy source cannot make another unavailable/disabled source
    # disappear from the overall coverage status. Distinguish an entirely
    # unavailable feed from a mixed (incomplete) multi-source snapshot.
    states_seen = {state["status"] for state in states}
    missing = {"disabled", "unavailable"}
    if "partial" in states_seen:
        coverage = "partial"
    elif states_seen <= missing:
        coverage = "unavailable"
    elif states_seen & missing:
        coverage = "partial"
    else:
        coverage = "available" if total else "empty"
    return {
        "status": coverage,
        "sources": states,
        "events": page,
        "total": total,
        "limit": limit,
        "offset": offset,
        "next_offset": offset + len(page) if offset + len(page) < total else None,
        "read_only": True,
        "bounded": True,
    }
