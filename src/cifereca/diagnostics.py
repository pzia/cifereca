from __future__ import annotations
import json
import logging
import threading
from collections import deque
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any

from .diagnostic_task_context import diagnostic_task_reference

# Backwards-compatible API names. Only GTK Diagnostics pseudonymizes explicit
# login/password values in its presentation layer; the Core and plugins must
# never alter source evidence in the journal, UI or exports.
_GENERIC_STATUSES = frozenset({
    "ok", "error", "warning", "failed", "success", "partial", "running",
    "complete", "completed", "attention_required", "retryable_error",
    "blocked", "not_due", "cancelled", "skipped", "timeout", "unknown",
})


def redact_text(text: str) -> str:
    """Deprecated name; return original evidence without masking."""
    return text


def redact(value: Any, key: str = "") -> Any:
    """Deprecated name; return original data without mutation or masking."""
    return value


def _core_journal_record(category: str, operation: str, status: str,
                         message: str, fields: dict[str, Any], *,
                         duration_ms: int | None = None) -> dict[str, Any]:
    """Carry an attested Core event on its existing logger call, once.

    The Core-owned file/console formatter serializes this extra metadata as
    DIAG_JSON; legacy handlers receive the original message unchanged.
    Values are never redacted by this logging path. The optional in-memory
    snapshot remains a temporary compatibility view, not a second writer.
    """
    payload: dict[str, Any] = {
        "schema_version": 1, "category": category,
        "operation": operation, "status": status,
        "message": message, "detail": dict(fields),
    }
    if type(fields.get("task_ref")) is str:
        payload["task_ref"] = fields["task_ref"]
    measured_ms = duration_ms if duration_ms is not None else fields.get("duration_ms")
    if type(measured_ms) is int and 0 <= measured_ms <= 86_400_000:
        payload["duration_ms"] = measured_ms
    return {"_cifereca_journal_payload": payload}


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
    verified_provider_connection_attempt: bool = False
    verified_source_sync: bool = False
    verified_kobo_adept: bool = False
    verified_numilog_startup: bool = False
    verified_kobo_backup: bool = False
    verified_browser_recorder: bool = False
    verified_reconciliation: bool = False
    verified_loan_download: bool = False
    verified_bnf_loan_action: bool = False
    verified_cover_failure: bool = False


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
        # The errors-only level must not discard an actual failure merely
        # because its producer normally reports at the "normal" level.
        # This is a severity filter, not a switch disabling error capture.
        if self.level == "errors":
            if type(status) is str and status not in {
                "error", "failed", "warning", "attention_required",
                "retryable_error", "blocked", "cancelled", "timeout",
                "partial",
            }:
                return
        elif self.LEVELS.get(level, 1) > self.LEVELS.get(self.level, 1):
            return
        # The compatibility snapshot must agree with the canonical journal.
        # Never mask outside GTK; reject unsupported plugin objects without
        # invoking their __str__, __repr__, __bool__ or __eq__ hooks.
        try:
            from .core.diagnostic_journal import journal_payload
            raw_detail = journal_payload(detail) if type(detail) is dict else {}
        except Exception:
            raw_detail = {}
        # BnF HTTP observations belong to the actual worker executing the GET.
        # This reference is Core-owned; request_ref and plugin-supplied detail
        # cannot claim task provenance. Leave unrelated generic events alone.
        bnf_http = (type(category) is str and category == "API"
                    and type(operation) is str and operation == "BnF HTTP"
                    and type(raw_detail.get("provider_id")) is str
                    and raw_detail["provider_id"] == "bnf-pret")
        correlate = bnf_http or (type(category) is str and category == "NUMILOG")
        entry = DiagnosticEntry(
            timestamp=datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            category=category[:160] if type(category) is str else "DIAGNOSTICS",
            operation=operation[:160] if type(operation) is str else "[opération masquée]",
            status=status if type(status) is str and status in _GENERIC_STATUSES else "unknown",
            duration_ms=duration_ms if type(duration_ms) is int and 0 <= duration_ms <= 86_400_000 else None,
            detail=raw_detail,
            task_ref=diagnostic_task_reference() if correlate else None,
        )
        with self._lock:
            self._items.append(entry)
        # The new canonical consumer reads exactly one physical log entry.
        # Preserve original JSON-compatible input; legacy in-memory snapshots
        # remain available for plugins not yet migrated to source='journal'.
        try:
            from .core.diagnostic_journal import journal_payload
            raw = {
                "schema_version": 1,
                "category": category if type(category) is str else "DIAGNOSTICS",
                "operation": operation if type(operation) is str else "",
                "status": status if type(status) is str else "unknown",
                "message": operation if type(operation) is str else "diagnostic",
                "detail": journal_payload(detail) if type(detail) is dict else {},
                **({"duration_ms": entry.duration_ms} if entry.duration_ms is not None else {}),
                **({"task_ref": entry.task_ref} if entry.task_ref else {}),
            }
            payload = json.dumps(raw, ensure_ascii=False, allow_nan=False)
            severity = logging.ERROR if status in {"error", "failed", "timeout"} else (
                logging.WARNING if status in {
                    "warning", "cancelled", "attention_required", "retryable_error",
                    "blocked", "partial",
                } else logging.INFO
            ) if type(status) is str else logging.WARNING
            logging.getLogger("cifereca").log(severity, "DIAG_JSON %s", payload)
        except Exception:
            # The persistent write can fail independently, but must never
            # change the provider/business result or stringify hostile data.
            pass

    def add_core_bnf_loan_action(self, event: object) -> None:
        """Core-attested terminal fact, never a generic plugin diagnostic."""
        from .core.diagnostic_bnf_loan_action import public_fields, log_message
        fields = public_fields(event)
        if not fields:
            return
        status = fields['status']
        if self.enabled and (self.level != 'errors' or status != 'success'):
            entry = DiagnosticEntry(
                timestamp=datetime.now(timezone.utc).isoformat(timespec='milliseconds'),
                category='LOAN', operation='bnf_loan_action',
                status='ok' if status == 'success' else status,
                duration_ms=fields['duration_ms'], detail=fields,
                verified_bnf_loan_action=True,
            )
            with self._lock:
                self._items.append(entry)
        try:
            severity = logging.INFO if status == 'success' else logging.ERROR if status == 'failed' else logging.WARNING
            message = log_message(fields)
            logging.getLogger('cifereca').log(
                severity, '%s', message,
                extra=_core_journal_record(
                    'LOAN', 'bnf_loan_action',
                    'ok' if status == 'success' else status, message, fields,
                ),
            )
        except Exception:
            pass

    def add_core_loan_download(self, event: object) -> None:
        """Core-only terminal result for ONE explicitly requested recovery.

        Generic add() cannot claim verified_loan_download. Strict projection
        and a constant log grammar reject untyped payloads. Observed loan IDs
        and sanitized causes are optional; no provider call is inferred.
        """
        from .core.diagnostic_loan_download import public_fields, log_message
        fields = public_fields(event)
        if not fields:
            return
        status = fields["status"]
        # A terminal provider refusal or download failure is an ERROR, not
        # a generic warning. Otherwise the canonical Diagnostics journal's
        # "Erreurs" filter hides precisely the incidents the user requested.
        # Cancellation/retry/attention remain warnings, success stays info.
        severity = (
            logging.INFO if status == "success" else
            logging.ERROR if status in {"failed", "refused"} else
            logging.WARNING
        )
        record = self.enabled and (self.level != "errors" or status != "success")
        if record or logging.getLogger("cifereca").isEnabledFor(severity):
            reference = diagnostic_task_reference()
            if reference:
                fields["task_ref"] = reference
        if record:
            entry = DiagnosticEntry(
                timestamp=datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                category="PNB" if fields["provider_id"] == "paris" else "LOAN",
                operation="download_loan", status="ok" if status == "success" else status,
                duration_ms=fields["duration_ms"], detail=fields,
                task_ref=fields.get("task_ref"),
                verified_loan_download=True,
            )
            with self._lock:
                self._items.append(entry)
        try:
            logger = logging.getLogger("cifereca")
            if logger.isEnabledFor(severity):
                message = log_message(fields)
                logger.log(
                    severity, "%s", message,
                    extra=_core_journal_record(
                        "PNB" if fields["provider_id"] == "paris" else "LOAN",
                        "download_loan", "ok" if status == "success" else status,
                        message, fields,
                    ),
                )
        except Exception:
            # Diagnostics are never allowed to alter an acquisition result.
            pass

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
        # This Core-attested Browser Recorder outcome previously existed only
        # in the volatile snapshot. Publish it once in the same physical
        # journal consumed by Diagnostics; never infer a provider or task ref.
        try:
            payload = {
                "schema_version": 1,
                "category": "PLUGIN",
                "operation": fields["operation"],
                "status": status,
                "message": OPERATIONS[fields["operation"]],
                "detail": {"domain": "browser_recorder", **fields},
                **({"duration_ms": fields["duration_ms"]} if "duration_ms" in fields else {}),
            }
            severity = (
                logging.ERROR if status == "error" else
                logging.WARNING if status == "warning" else logging.INFO
            )
            logging.getLogger("cifereca").log(
                severity, "DIAG_JSON %s",
                json.dumps(payload, ensure_ascii=False, allow_nan=False),
            )
        except Exception:
            # Diagnostics must never alter the plugin operation's result.
            pass


    def add_core_cover_failure(self, code: str, *, duration_ms: int) -> None:
        """One attested image-download failure, not an EPUB acquisition.

        A generic plugin diagnostic cannot attest a cover failure reason.
        Publish a single Core-owned canonical journal occurrence; the
        in-memory snapshot remains read-only legacy compatibility.
        """
        from .core.diagnostic_cover_failure import public_fields, public_message
        fields = public_fields({"code": code, "duration_ms": duration_ms})
        if not fields or not self.enabled:
            return
        message = public_message(fields)
        if message is None:
            return
        entry = DiagnosticEntry(
            timestamp=datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            category="COVER", operation="download", status="error",
            duration_ms=fields["duration_ms"], detail={"code": fields["code"]},
            verified_cover_failure=True,
        )
        with self._lock:
            self._items.append(entry)
        try:
            logger = logging.getLogger("cifereca")
            if logger.isEnabledFor(logging.ERROR):
                logger.log(
                    logging.ERROR, "%s", message,
                    extra=_core_journal_record(
                        "COVER", "cover_download_failure", "error", message,
                        {"code": fields["code"]}, duration_ms=fields["duration_ms"],
                    ),
                )
        except Exception:
            # Observability cannot interfere with the visual fallback.
            pass

    def add_core_provider_connection_attempt(self, event: object) -> None:
        """P0 #3101: a single Core request emits attested phase records.

        A random operation_ref joins these records across Diagnostics and the
        canonical app log. Neither event_id nor timestamps are correlation
        keys. No credential, borrower or HTTP-login-count is published.
        """
        from .core.diagnostic_provider_connection_attempt import (
            public_fields, log_message,
        )
        fields = public_fields(event)
        if not fields:
            return
        severity = (
            logging.ERROR if fields["status"] == "failed"
            else logging.WARNING if fields["status"] in {"cancelled", "skipped"}
            else logging.INFO
        )
        if self.enabled and (self.level != "errors" or severity >= logging.ERROR):
            entry = DiagnosticEntry(
                timestamp=datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                category="PLUGIN", operation="provider_connect",
                status=(
                    "error" if fields["status"] == "failed"
                    else "warning" if fields["status"] in {"skipped", "cancelled"}
                    else "ok" if fields["status"] == "success" else "unknown"
                ),
                duration_ms=fields["duration_ms"], detail=fields,
                verified_provider_connection_attempt=True,
            )
            with self._lock:
                self._items.append(entry)
        try:
            message = log_message(fields)
            logger = logging.getLogger("cifereca")
            if message is not None and logger.isEnabledFor(severity):
                logger.log(
                    severity, "%s", message,
                    extra=_core_journal_record(
                        "PLUGIN", "provider_connect",
                        "error" if fields["status"] == "failed"
                        else "warning" if fields["status"] in {"skipped", "cancelled"}
                        else "ok" if fields["status"] == "success" else "unknown",
                        message, fields,
                    ),
                )
        except Exception:
            pass

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
        time_ms = (duration_ms if type(duration_ms) is int
                   and 0 <= duration_ms <= 600000 else None)
        if self.enabled:
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
                message = public_connection_log_message(fields)
                logger.error(
                    "%s", message,
                    extra=_core_journal_record(
                        "PLUGIN",
                        "public provider connect" if singleton else "provider connect",
                        "error", message, fields, duration_ms=time_ms,
                    ),
                )
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
                message = public_connection_success_log_message(fields)
                logger.info(
                    "%s", message,
                    extra=_core_journal_record(
                        "PLUGIN",
                        "public provider connected" if singleton else "provider connected",
                        "ok", message, fields,
                        duration_ms=duration_ms,
                    ),
                )
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
            logging.WARNING if status in {"cancelled", "partial"} else logging.INFO)
        record = self.enabled and (
            self.level != "errors" or status in {"failed", "cancelled", "partial"}
        )
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
            message = log_message(fields)
            logger.log(
                severity, "%s", message,
                extra=_core_journal_record(
                    "SYNC", "source sync", status, message, fields,
                ),
            )
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
            message = log_message(fields)
            logger.log(
                severity, "%s", message,
                extra=_core_journal_record(
                    "ADEPT", "kobo adept", fields["status"], message, fields,
                ),
            )
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
        # Preserve the closed audit summary after restart without widening
        # the capture of personal files, borrowers, profiles or network data.
        try:
            from .core.diagnostic_reconciliation import public_reconciliation_log_message
            message = public_reconciliation_log_message(fields)
            if message is not None:
                severity = (logging.INFO if status == "complete"
                            else logging.ERROR if status == "failed"
                            else logging.WARNING)
                logger = logging.getLogger("cifereca")
                if logger.isEnabledFor(severity):
                    logger.log(
                        severity, "%s", message,
                        extra=_core_journal_record(
                            "LIBRARY", "library_reconciliation_audit",
                            "ok" if status == "complete" else status, message, fields,
                        ),
                    )
        except Exception:
            # Diagnostic persistence cannot alter a read-only audit.
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
                if row.get("verified_loan_download") is False:
                    row.pop("verified_loan_download", None)
                if row.get("verified_cover_failure") is False:
                    row.pop("verified_cover_failure", None)
            return rows

    def text(self, version: str, extra: dict[str, Any] | None = None) -> str:
        """Legacy local text export: retain all JSON-compatible values raw.

        Only the GTK Diagnostics presentation may pseudonymize explicit
        login/password values; this export must not perform any masking.
        """
        from .core.diagnostic_journal import journal_payload

        def display(value: Any) -> str:
            raw = journal_payload(value)
            return (json.dumps(raw, ensure_ascii=False, allow_nan=False)
                    if type(raw) in (dict, list) else str(raw))

        lines = [
            f"cifereca v{version}",
            f"Generated: {datetime.now(timezone.utc).isoformat(timespec='milliseconds')}",
            f"Diagnostics: {'enabled' if self.enabled else 'disabled'} level={self.level}",
        ]
        if type(extra) is dict:
            for key, value in extra.items():
                if type(key) is str:
                    lines.append(f"{key}: {display(value)}")
        lines.append("")
        for item in self.snapshot():
            lines.append(f"[{item['timestamp']}] {item['category']} {item['operation']} status={item['status']}")
            if item.get("task_ref"):
                lines.append("task_ref=" + item["task_ref"])
            if item.get("duration_ms") is not None:
                lines.append(f"duration={item['duration_ms']}ms")
            detail = item.get("detail") or {}
            for key, value in detail.items():
                if type(key) is str:
                    lines.append(f"{key}={display(value)}")
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"
