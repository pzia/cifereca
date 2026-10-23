from __future__ import annotations
import json
import logging
import re
import threading
from collections import deque
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any

from .diagnostic_task_context import numilog_task_reference

# Public redact()/redact_text() is also used by book/sync UI. Its legacy
# contract must not mask a book title or a non-sensitive presence boolean.
SENSITIVE_KEYS = re.compile(r"pass|password|cookie|token|secret|readerid|barcode|session|authorization", re.I)

# Additional *generic diagnostic collector* guard only. This never modifies
# the public redaction helper used by Library and Synchronisation views.
_DIAGNOSTIC_PRIVATE_KEYS = re.compile(
    r"(?:^|[_-])(?:credential(?:[_-]?id)?|account[_-]?id|identity[_-]?id"
    r"|profile[_-]?id|loan[_-]?id|source[_-]?item[_-]?id|book[_-]?title"
    r"|title|filename|file[_-]?path|local[_-]?path|acsm|license|licence"
    r"|signed[_-]?url|delivery[_-]?url|source[_-]?url|headers?|api[_-]?key)"
    r"(?:$|[_-])",
    re.I,
)


def _redact_generic_diagnostic_detail(detail: dict) -> dict:
    """Fail closed on private values of otherwise well-formed free-form keys.

    This is supplementary to each producer's typed public projection. Pure
    booleans ending in _present are non-identifying existence observations;
    preserve these for established ADEPT diagnostics without retaining IDs.
    """
    def guard(value, depth=0):
        if depth > 5:
            return "<redacted>"
        if type(value) is dict:
            result = {}
            for key, item in list(value.items())[:64]:
                # A dict key is data as well: never retain a URL, email,
                # private path or hostile object disguised as a field name.
                if (type(key) is not str or len(key) > 64 or
                        re.fullmatch(r"[a-z][a-z0-9_]*", key, re.ASCII) is None):
                    continue
                if (_DIAGNOSTIC_PRIVATE_KEYS.search(key)
                        and not (key.endswith("_present") and type(item) is bool)):
                    result[key] = "<redacted>"
                else:
                    result[key] = guard(item, depth + 1)
            return result
        if type(value) is list:
            return [guard(item, depth + 1) for item in value[:100]]
        if type(value) in (str, bool, int, float) or value is None:
            return value
        return "<redacted>"

    return guard(redact(detail))


_GENERIC_STATUSES = frozenset({
    "ok", "error", "warning", "failed", "success", "partial", "running",
    "complete", "completed", "attention_required", "retryable_error",
    "blocked", "not_due", "cancelled", "skipped", "timeout", "unknown",
})


def _legacy_text_key(value: object) -> str:
    """Never render free-form dictionary keys into the support text export."""
    if (type(value) is str and len(value) <= 64
            and re.fullmatch(r"[a-z][a-z0-9_]*", value, re.ASCII)):
        return value
    return "[champ masqué]"



def redact_text(text: str) -> str:
    # The redactor is a last-resort guard for free-text coming from providers,
    # exceptions or plugin logs. It cannot replace typed, closed diagnostics.
    # Never print signed URLs, private filesystem locations or email addresses.
    text = re.sub(r"https?://[^\s<>\"']+", lambda m: _redact_url(m.group()), text, flags=re.I)
    text = re.sub(r"(?i)file://[^\s<>\"']+", "<redacted-path>", text)
    text = re.sub(
        r"(?i)(?<![\w:/])/(?:home|users|root|tmp|var|mnt|media|run)/[^\s,;:\"'<>)}\]]+",
        "<redacted-path>", text,
    )
    text = re.sub(
        r"(?i)\b[a-z]:[\\/](?:[^\s,;:\"'<>)}\]]+)",
        "<redacted-path>", text,
    )
    text = re.sub(
        r"(?i)(?<![\w.+-])[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}(?![\w.-])",
        "<redacted-email>", text,
    )
    text = re.sub(r"(?i)\bBearer\s+[^\s,;]+", "Bearer <redacted>", text)
    text = re.sub(r"(?i)\b(password|cookie|token|secret|authorization|session|barcode|readerid)\b\s*[:=]\s*[^\s,;]+", r"\1=<redacted>", text)
    text = re.sub(r"(?i)([\"']?(?:password|cookie|token|secret|authorization|session|barcode|readerid)[\"']?\s*[:=]\s*)(?:[\"'][^\"']*[\"']|[^\s,;}]+)", r"\1<redacted>", text)
    return text


def _redact_url(url):
    from urllib.parse import urlsplit
    try:
        parsed = urlsplit(url)
        # Preserve non-sensitive public URLs (historical Plugin API contract)
        # while hiding known borrower/account routes even without a query.
        private_route = re.search(
            r"(?i)/(?:LOAN|reader|account|users?|private(?:[-_][^/]+)?|orders?|borrow|fulfil(?:l)?ment|acquisition|URLLink\.acsm)(?:/|$)",
            parsed.path,
        )
        if parsed.query or parsed.fragment or parsed.username or private_route:
            return f"{parsed.scheme}://{parsed.hostname}/<redacted>"
        return url
    except ValueError:
        return "<redacted-url>"


def redact(value: Any, key: str = "") -> Any:
    if SENSITIVE_KEYS.search(key):
        return "<redacted>"
    if isinstance(value, dict):
        return {k: redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v, key) for v in value]
    if isinstance(value, str):
        text = redact_text(value)
        return text[:500] + "…" if len(text) > 500 else text
    return value


@dataclass
class DiagnosticEntry:
    timestamp: str
    category: str
    operation: str
    status: str
    duration_ms: int | None = None
    detail: dict[str, Any] | None = None
    task_ref: str | None = None
    # Only Core's fixed connection-failure producer may set this field.
    # Arbitrary plugin detail={"provider_id": ...} is NOT an attestation.
    verified_public_provider_id: str | None = None
    verified_public_provider_success: bool = False
    verified_source_sync: bool = False
    verified_kobo_adept: bool = False
    verified_numilog_startup: bool = False
    verified_kobo_backup: bool = False
    verified_browser_recorder: bool = False
    verified_reconciliation: bool = False


class Diagnostics:
    LEVELS = {"errors": 0, "normal": 1, "detailed": 2, "verbose": 2, "trace": 3}

    def __init__(self, enabled: bool = True, level: str = "normal", max_entries: int = 500):
        self.enabled = enabled
        self.level = level if level in self.LEVELS else "normal"
        self._items: deque[DiagnosticEntry] = deque(maxlen=max_entries)
        self._lock = threading.Lock()

    def add(self, category: str, operation: str, status: str = "ok", duration_ms: int | None = None,
            detail: dict[str, Any] | None = None, level: str = "normal") -> None:
        if not self.enabled:
            return
        if self.LEVELS.get(level, 1) > self.LEVELS.get(self.level, 1):
            return
        if self.level == "errors" and type(status) is str and status == "ok":
            return
        # The generic collector is optional. Malformed plugin data must not
        # crash a workflow or gain a printable representation of a private
        # object by way of its __str__/__bool__/__eq__ hooks.
        try:
            safe_detail = _redact_generic_diagnostic_detail(detail) if type(detail) is dict else {}
        except Exception:
            safe_detail = {}
        entry = DiagnosticEntry(
            timestamp=datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            category=redact_text(category)[:160] if type(category) is str else "DIAGNOSTICS",
            operation=redact_text(operation)[:160] if type(operation) is str else "[opération masquée]",
            status=status if type(status) is str and status in _GENERIC_STATUSES else "unknown",
            duration_ms=duration_ms if type(duration_ms) is int and 0 <= duration_ms <= 86_400_000 else None,
            detail=safe_detail,
            task_ref=numilog_task_reference() if type(category) is str and category == "NUMILOG" else None,
        )
        with self._lock:
            self._items.append(entry)

    def add_core_browser_recorder_result(self, operation: str, code: str, *,
                                         duration_ms=None, output_bytes=None) -> None:
        """Attest an explicitly requested Browser Recorder GUI operation.

        Only fixed code/operation identifiers cross into the public broker
        feed. The generic add() path cannot claim this provenance and private
        Playwright/pip exceptions, paths and proxy values never enter memory.
        Private ZIP exports additionally accept strict integers for elapsed
        milliseconds (0..24 h) and encrypted output bytes (0..16 MiB).
        Missing or invalid metrics are omitted, never coerced or logged.
        """
        from .core.browser_recorder_diagnostics import (
            OPERATIONS, public_fields, public_status,
        )
        if not self.enabled:
            return
        fields = public_fields(operation, code, duration_ms=duration_ms, output_bytes=output_bytes)
        if fields is None:
            return
        status = public_status(fields)
        # Errors-only is an output severity filter, NOT a blanket refusal
        # of normal-level producers: an explicit failed browser operation
        # must remain discoverable even with noise minimized.
        if self.level == "errors":
            if status == "ok":
                return
        elif self.LEVELS["normal"] > self.LEVELS[self.level]:
            return
        entry = DiagnosticEntry(
            timestamp=datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            category="PLUGIN", operation=OPERATIONS[fields["operation"]],
            duration_ms=fields.get("duration_ms"),
            status=status, detail=fields, verified_browser_recorder=True,
        )
        with self._lock:
            self._items.append(entry)

    def add_core_provider_connect_failure(
        self, provider_id: str, *, singleton: bool = False,
        duration_ms: int | None = None, code: str = "provider_connect_failed",
    ) -> None:
        """Emit one closed, Core-verified failure to both independent channels.

        The caller must have matched the public provider id and failure code
        to the Core registry/connection decision. Generic add() never grants
        this provenance; no account, credential, exception or task reference
        is published. A disabled collector does not disable the bounded log.
        """
        from .core.public_provider_diagnostics import (
            UNKNOWN_PROVIDER, CONNECT_FAILURE_CODE, MISSING_PROVIDER_CODE,
            canonical_public_provider_id, public_connection_fields,
            public_connection_log_message,
        )
        slug = canonical_public_provider_id(provider_id) or UNKNOWN_PROVIDER
        fixed_code = (
            code if type(code) is str and code in {
                CONNECT_FAILURE_CODE, MISSING_PROVIDER_CODE,
            } else CONNECT_FAILURE_CODE
        )
        fields = public_connection_fields({
            "provider_id": slug, "code": fixed_code,
        })
        if not fields:
            return
        logger = logging.getLogger("cifereca")
        if self.enabled:
            time_ms = (duration_ms if type(duration_ms) is int
                       and 0 <= duration_ms <= 600000 else None)
            entry = DiagnosticEntry(
                timestamp=datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                category="PLUGIN",
                operation="public provider connect" if singleton else "provider connect",
                status="error", duration_ms=time_ms,
                detail=fields,
                verified_public_provider_id=slug,
            )
            with self._lock:
                self._items.append(entry)
        try:
            if logger.isEnabledFor(logging.ERROR):
                logger.error(public_connection_log_message(fields))
        except Exception:
            # Optional logging is never allowed to turn a connection failure
            # into a different outcome or leak the handler's private message.
            pass

    def add_core_provider_connect_success(
        self, provider_id: str, *, singleton: bool = False,
        account_count: int = 0, duration_ms: int | None = None,
    ) -> None:
        """Core-verified result of ONE successful connect and persistence.

        Does not claim that the previous synchronisation succeeded. Neither
        account identifier nor identity is retained. Generic add() never
        grants the provenance flag required by the public event feed.
        """
        from .core.public_provider_diagnostics import (
            CONNECTED_CODE, public_connection_success_fields,
            public_connection_success_log_message,
        )
        fields = public_connection_success_fields({
            "provider_id": provider_id,
            "code": CONNECTED_CODE,
            "source_kind": "singleton_public" if singleton else "configured",
            "account_count": account_count,
        })
        if not fields:
            return
        if self.enabled and self.level != "errors":
            entry = DiagnosticEntry(
                timestamp=datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                category="PLUGIN",
                operation="public provider connected" if singleton else "provider connected",
                status="ok",
                duration_ms=(duration_ms if type(duration_ms) is int
                             and 0 <= duration_ms <= 600000 else None),
                detail=fields,
                verified_public_provider_success=True,
            )
            with self._lock:
                self._items.append(entry)
        # Normal log only. Unlike unstructured TRACE, this fixed schema is
        # retained at INFO. A disabled collector does not disable local logs.
        try:
            logger = logging.getLogger("cifereca")
            if logger.isEnabledFor(logging.INFO):
                logger.info("%s", public_connection_success_log_message(fields))
        except Exception:
            pass

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    def add_core_numilog_startup(self, detail: object) -> None:
        """Core-only attested startup facts; generic add() is not provenance.

        The startup producer already writes the independent canonical log.
        This entry only publishes the same checked fields to the in-memory
        collector, never a signed URL, exception or provider payload.
        """
        from .core.numilog_startup_diagnostics import public_fields

        fields = public_fields(detail)
        if not fields or not self.enabled:
            return
        status = ("error" if fields["status"] in {"failed", "partial"}
                  else "warning" if fields["status"] == "blocked" else "ok")
        if self.level == "errors" and status == "ok":
            return
        entry = DiagnosticEntry(
            timestamp=datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            category="NUMILOG", operation="startup_materialization",
            status=status, detail=fields, task_ref=fields.get("task_ref"),
            verified_numilog_startup=True,
        )
        with self._lock:
            self._items.append(entry)

    def add_core_kobo_backup_decision(self, detail: object) -> None:
        """Record only the closed Core opening decision as attested evidence.

        Generic Diagnostics.add() cannot attest a Kobo decision merely by
        choosing the same category and operation. This producer does not
        persist any provider-supplied field or invent a shared task reference.
        The existing independent local TRACE remains emitted by the caller.
        """
        from .core.diagnostic_kobo_projection import public_kobo_backup_decision

        if not self.enabled or self.LEVELS["normal"] > self.LEVELS[self.level]:
            return
        fields = public_kobo_backup_decision(detail)
        if not fields:
            return
        safe_detail = {
            "status": fields["status"],
            "persisted_enabled": fields["enabled"],
            "connected_kobo": fields["connected"],
            "eligible_kobo": fields["eligible"],
            "interval_minutes": fields["interval_minutes"],
        }
        entry = DiagnosticEntry(
            timestamp=datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            category="KOBO", operation="automatic backup decision",
            status="warning" if fields["status"] in {"partial", "error"} else "ok",
            detail=safe_detail, verified_kobo_backup=True,
        )
        with self._lock:
            self._items.append(entry)

    def add_core_source_sync(self, provider_id: str, *, phase: str, status: str,
                             code: str, duration_ms: int | None = None,
                             counts: dict | None = None,
                             timings: dict | None = None) -> None:
        """Internal producer; caller attests the provider via Core's registry.

        Generic plugin add() cannot set the publication provenance flag.
        The log and collector receive the same validated facts independently.
        """
        from .core.diagnostic_source_sync import public_fields, log_message
        from .diagnostic_task_context import diagnostic_task_reference
        logger = logging.getLogger("cifereca")
        severity = logging.ERROR if status == "failed" else (
            logging.WARNING if status == "cancelled" else logging.INFO)
        record = self.enabled and (self.level != "errors" or status in {"failed", "cancelled"})
        if not record and not logger.isEnabledFor(severity):
            return
        fields = dict(schema_version=1, provider_id=provider_id,
                      operation_kind="source_sync", phase=phase, status=status, code=code)
        if type(duration_ms) is int and 0 <= duration_ms <= 86_400_000:
            fields["duration_ms"] = duration_ms
        if type(counts) is dict:
            for key in ("loans", "library"):
                value = counts.get(key)
                if type(value) is int and 0 <= value <= 1_000_000:
                    fields[key] = value
        if type(timings) is dict and phase == "completed":
            from .core.source_sync_timing import TIMING_FIELDS
            for key in TIMING_FIELDS:
                value = timings.get(key)
                if type(value) is int and 0 <= value <= 86_400_000:
                    fields[key] = value
        fields = public_fields(fields)
        if not fields:
            return
        reference = diagnostic_task_reference()
        if reference is not None:
            fields["task_ref"] = reference
        fields = public_fields(fields)
        if not fields:
            return
        if record:
            entry = DiagnosticEntry(
                timestamp=datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                category="SYNC", operation="source sync", status=status,
                duration_ms=fields.get("duration_ms"), detail=fields,
                task_ref=fields.get("task_ref"), verified_source_sync=True,
            )
            with self._lock:
                self._items.append(entry)
        try:
            logger.log(severity, "%s", log_message(fields))
        except Exception:
            pass

    def add_core_kobo_adept(self, detail: object, status: str, *, level: str = "normal") -> None:
        """Internal terminal producer; the manager attests installed identity."""
        from .core.diagnostic_adept import from_runtime, public_fields, log_message
        from .diagnostic_task_context import diagnostic_task_reference
        fields = from_runtime(detail, status)
        if not fields:
            return
        logger = logging.getLogger("cifereca")
        severity = logging.ERROR if fields["status"] == "failed" else logging.INFO
        record = (self.enabled and self.LEVELS.get(level, 1) <= self.LEVELS[self.level]
                  and (self.level != "errors" or status == "error"))
        if not record and not logger.isEnabledFor(severity):
            return
        reference = diagnostic_task_reference()
        if reference is not None:
            fields["task_ref"] = reference
        fields = public_fields(fields)
        if not fields:
            return
        if record:
            entry = DiagnosticEntry(
                timestamp=datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                category="ADEPT", operation="kobo adept", status=fields["status"],
                duration_ms=fields.get("duration_ms"), detail=fields,
                task_ref=fields.get("task_ref"), verified_kobo_adept=True,
            )
            with self._lock:
                self._items.append(entry)
        try:
            logger.log(severity, "%s", log_message(fields))
        except Exception:
            pass

    def add_core_reconciliation_result(self, result: object, *, duration_ms: int | None = None) -> None:
        """Core-only terminal result: never store private library evidence.

        Called once per broker attempt with its already-redacted summary.
        A generic add() row can never acquire this attestation flag.
        """
        from .core.diagnostic_reconciliation import from_audit_result, public_fields

        if not self.enabled:
            return
        fields = public_fields(from_audit_result(result, duration_ms))
        if not fields:
            return
        status = fields["status"]
        if self.level == "errors" and status == "complete":
            return
        entry = DiagnosticEntry(
            timestamp=datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            category="LIBRARY", operation="library_reconciliation_audit",
            status="ok" if status == "complete" else status,
            duration_ms=fields.get("duration_ms"), detail=fields,
            verified_reconciliation=True,
        )
        with self._lock:
            self._items.append(entry)
        # Record only the already-closed aggregate in the rotating local log.
        # This must never change the offline audit result.
        try:
            from .core.diagnostic_reconciliation import public_reconciliation_log_message
            message = public_reconciliation_log_message(fields)
            if message is not None:
                severity = (logging.INFO if status == "complete"
                            else logging.ERROR if status == "failed"
                            else logging.WARNING)
                logger = logging.getLogger("cifereca")
                if logger.isEnabledFor(severity):
                    logger.log(severity, "%s", message)
        except Exception:
            pass

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = [asdict(x) for x in self._items]
            for row in rows:
                if row.get("task_ref") is None:
                    row.pop("task_ref", None)
                if row.get("verified_public_provider_id") is None:
                    row.pop("verified_public_provider_id", None)
                if row.get("verified_public_provider_success") is False:
                    row.pop("verified_public_provider_success", None)
                if row.get("verified_source_sync") is False:
                    row.pop("verified_source_sync", None)
                if row.get("verified_kobo_adept") is False:
                    row.pop("verified_kobo_adept", None)
                if row.get("verified_numilog_startup") is False:
                    row.pop("verified_numilog_startup", None)
                if row.get("verified_kobo_backup") is False:
                    row.pop("verified_kobo_backup", None)
                if row.get("verified_browser_recorder") is False:
                    row.pop("verified_browser_recorder", None)
                if row.get("verified_reconciliation") is False:
                    row.pop("verified_reconciliation", None)
            return rows

    def text(self, version: str, extra: dict[str, Any] | None = None) -> str:
        lines = [
            f"cifereca v{version}",
            f"Generated: {datetime.now(timezone.utc).isoformat(timespec='milliseconds')}",
            f"Diagnostics: {'enabled' if self.enabled else 'disabled'} level={self.level}",
        ]
        if type(extra) is dict:
            for key, value in extra.items():
                safe_key = _legacy_text_key(key)
                if safe_key == "[champ masqué]":
                    lines.append("[champ masqué]: <redacted>")
                    continue
                try:
                    safe_value = redact(value, safe_key)
                except Exception:
                    safe_value = "<redacted>"
                lines.append(f"{safe_key}: {safe_value}")
        lines.append("")
        for item in self.snapshot():
            lines.append(f"[{item['timestamp']}] {item['category']} {item['operation']} status={item['status']}")
            if item.get("task_ref"):
                lines.append("task_ref=" + item["task_ref"])
            if item.get("duration_ms") is not None:
                lines.append(f"duration={item['duration_ms']}ms")
            detail = item.get("detail") or {}
            if detail:
                for k, v in detail.items():
                    safe_key = _legacy_text_key(k)
                    if safe_key == "[champ masqué]":
                        lines.append("[champ masqué]=<redacted>")
                        continue
                    lines.append(f"{safe_key}={json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v}")
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"
