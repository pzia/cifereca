"""Closed, optional startup telemetry. Never an acquisition authority (#2131)."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import json
import logging
import re
import secrets

from ..diagnostic_task_context import numilog_task_reference
from ..plugin_api import Cancelled

PHASES = frozenset({"admission", "durable_claim", "manifest_fetch", "rights_mirror",
    "preflight", "acquisition", "adept", "index", "receipt_finalization", "completed"})
CODES = frozenset({"started", "complete", "not_due", "consent_revoked", "admission_failed",
    "claim_failed", "manifest_failed", "manifest_invalid", "rights_mirror_failed",
    "preflight_failed", "acquisition_failed", "adept_failed", "index_failed",
    "receipt_finalize_failed", "interrupted", "adept_complete",
    # Refusal boundaries in the locked Numilog acquisition executor. These
    # contain no exception string, URL, filename, product ID or account.
    "acquisition_lock_busy", "acquisition_source_unavailable",
    "acquisition_local_conflict", "acquisition_root_unavailable",
    "acquisition_file_conflict", "acquisition_publication_guard",
    "acquisition_admission_failed", "acquisition_delivery_failed",
    "acquisition_post_delivery_failed", "acquisition_artifact_invalid",
    "acquisition_receipt_failed"})
STATUSES = frozenset({"running", "complete", "partial", "failed", "blocked", "not_due"})
# Per-purchase *position within this one authenticated manifest* + closed outcome.
# Neither a product ID nor a stable cross-run fingerprint ever leaves the Core.
# Maximum eight listed decisions prevents private-library enumeration; further
# purchases are represented only by the existing bounded totals.
ITEM_DECISIONS = frozenset({
    "already_local_declared", "declared_needs_review",
    "already_local_verified", "not_downloadable",
    "needs_reconciliation", "purchase_unproven", "source_not_ready",
    "evidence_unavailable", "source_changed", "invalid_source",
    "invalid_item_id", "preflight_failed", "consent_revoked",
    "quota_reached", "acquired", "acquisition_failed",
    "recovery_scope_invalid", "recovery_right_unproven",
    "recovery_receipts_ambiguous", "recovery_foreign_receipt",
    "recovery_already_attempted", "recovery_receipt_pending",
    "recovery_foreign_placement", "recovery_evidence_unavailable",
})

COUNTS = ("attempted", "downloaded", "skipped", "failed")
_KEY = "sync.source.numilog.materialization.diagnostic.v1."
_CLOCK = "sync.source.numilog.materialization.last_start.v1."
_ACTIVE = ContextVar("numilog_startup_diagnostic", default=None)
_LOG = logging.getLogger("cifereca")
_LOG_PREFIX = "TRACE numilog startup_materialization "


def public_fields(detail):
    """All-or-nothing validation; never coerce or stringify untrusted fields."""
    if type(detail) is not dict:
        return {}
    allowed = {"phase", "cause_phase", "code", "status", "task_kind", *COUNTS,
               "task_ref", "run_ref", "item_decisions", "items_truncated"}
    if set(detail) - allowed:
        return {}
    for key, values in (("phase", PHASES), ("cause_phase", PHASES),
                        ("code", CODES), ("status", STATUSES)):
        if type(detail.get(key)) is not str or detail[key] not in values:
            return {}
    if type(detail.get("task_kind")) is not str or detail["task_kind"] != "source-materialize":
        return {}
    if any(type(detail.get(k)) is not int or not 0 <= detail[k] <=
           (2048 if k == "skipped" else 9 if k == "failed" else 8) for k in COUNTS):
        return {}
    # The auxiliary schema must not turn a terminal failure into success
    # or advertise impossible totals, even when read outside the GTK view.
    if (detail["downloaded"] > detail["attempted"]
            or detail["failed"] > detail["attempted"] + 2
            or detail["status"] == "complete" and detail["failed"] != 0
            or detail["status"] == "not_due" and (
                detail["attempted"] != 0 or detail["downloaded"] != 0
                or detail["failed"] != 0
            )):
        return {}
    item_decisions = detail.get("item_decisions")
    if item_decisions is not None:
        if (type(item_decisions) is not list
                or len(item_decisions) > 8
                or any(
                    type(row) is not dict or set(row) != {"position", "decision"}
                    or type(row.get("position")) is not int
                    or not 1 <= row["position"] <= 2048
                    or type(row.get("decision")) is not str
                    or row["decision"] not in ITEM_DECISIONS
                    for row in item_decisions
                )
                or [row["position"] for row in item_decisions] != sorted(
                    {row["position"] for row in item_decisions}
                )):
            return {}
    if "items_truncated" in detail and (
        type(detail["items_truncated"]) is not bool
        or (detail["items_truncated"] is True and
            (item_decisions is None or len(item_decisions) != 8))
    ):
        return {}
    for key in ("task_ref", "run_ref"):
        if key in detail and (type(detail[key]) is not str or
                              re.fullmatch(r"[0-9a-f]{12}", detail[key]) is None):
            return {}
    return dict(detail)


def public_log_fields(body: object) -> dict:
    """Read exactly the canonical, bounded Core emission, never arbitrary JSON.

    Requiring canonical encoding also rejects duplicate keys, trailing text,
    whitespace variants and nonfinite constants before any public projection.
    """
    if type(body) is not str or len(body) > 2048 or not body.startswith(_LOG_PREFIX):
        return {}
    encoded = body[len(_LOG_PREFIX):]
    try:
        fields = public_fields(json.loads(encoded))
    except (ValueError, TypeError, RecursionError):
        return {}
    if not fields or json.dumps(fields, separators=(",", ":"), sort_keys=True) != encoded:
        return {}
    return fields


def _source(source_id):
    return type(source_id) is str and re.fullmatch(r"sync:[0-9a-f]{24}", source_id) is not None


def save(storage, source_id, clock, fields, *, claimed=False):
    """Auxiliary evidence, guarded by the same clock; never finalize a receipt.

    A failed receipt write remains visibly running in the authoritative ledger.
    This independent diagnostic can still explain that failure. A late worker
    cannot overwrite the diagnostic of a newer durable claim.
    """
    if not _source(source_id) or not public_fields(fields):
        return
    if clock is not None and (type(clock) is not str or not clock.isdecimal() or len(clock) > 11):
        return
    with storage._lock, storage._connect() as db:
        db.execute("BEGIN IMMEDIATE")
        current = db.execute("SELECT value FROM metadata WHERE key=?", (_CLOCK + source_id,)).fetchone()
        if (current[0] if current else None) != clock:
            return
        if not claimed:
            receipt = db.execute("SELECT value FROM metadata WHERE key=?",
                ("sync.source.numilog.materialization.result.v1." + source_id,)).fetchone()
            if receipt is not None:
                try:
                    prior_status = json.loads(receipt[0]).get("status")
                    if prior_status == "running":
                        return
                    if (fields["status"] == "not_due"
                            and prior_status in {"failed", "partial", "blocked"}):
                        # A throttled later opening did no work. Keep the
                        # cause/phase of the last *actual* durable failure,
                        # which would otherwise vanish from the UI while the
                        # SQLite failure receipt remains unchanged.
                        return
                except (ValueError, TypeError, AttributeError):
                    return
        value = json.dumps({"clock": clock, "detail": fields}, separators=(",", ":"), sort_keys=True)
        db.execute("INSERT INTO metadata(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                   (_KEY + source_id, value))


def read(storage, source_id):
    """Passive SQL snapshot only; no network, paths, profile or retry."""
    if not _source(source_id):
        return {}
    try:
        with storage._lock, storage._connect() as db:
            rows = dict(db.execute("SELECT key,value FROM metadata WHERE key IN (?,?)",
                                   (_CLOCK + source_id, _KEY + source_id)).fetchall())
        raw = rows.get(_KEY + source_id)
        if type(raw) is not str or len(raw) > 2048:
            return {}
        data = json.loads(raw)
        if type(data) is not dict or set(data) != {"clock", "detail"} or data["clock"] != rows.get(_CLOCK + source_id):
            return {}
        return public_fields(data["detail"])
    except Exception:
        return {}


def local_proof(account, source_id, item_id, gate, *, exc_info=False, **evidence):
    """Journal an existing local check, never fetch or classify new evidence.

    Raw facts stay separate from the bounded legacy UI counters. The exact
    purchase and task reference correlate this check without a new operation,
    guessed owner, retry or acquisition permission.
    """
    try:
        from ..diagnostics import _core_journal_record
        fields = dict(
            schema_version=1, operation_kind="local_presence_check", gate=gate,
            provider_id="numilog", source_id=source_id, item_id=item_id,
            identity_id=account.identity_id, credential_id=account.credential_id,
            account_id=account.unique_id, **evidence,
        )
        reference = numilog_task_reference()
        if reference is not None:
            fields["task_ref"] = reference
        # These are business check outcomes, visible at normal verbosity;
        # a TRACE prefix would be discarded by the real journal filter.
        message = "Numilog local_presence_check " + json.dumps(
            fields, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        )
        _LOG.log(
            logging.INFO if gate in {"verified", "file_fingerprint_verified"}
            else logging.WARNING, "%s", message,
            exc_info=exc_info,
            extra=_core_journal_record(
                "NUMILOG", "local_presence_check",
                "ok" if gate in {"verified", "file_fingerprint_verified"} else "warning", message, fields,
            ),
        )
    except Cancelled:
        raise
    except Exception:
        pass  # Optional diagnostics must never grant or refuse acquisition.


class StartupTrace:
    def __init__(self, service, source_id):
        self.service, self.source_id = service, source_id
        self.phase, self.cause = "admission", None
        self.counts = dict.fromkeys(COUNTS, 0)
        self.item_decisions = []
        self.items_truncated = False
        self.refs = {}
        self.clock = None
        self.claimed = False
        try:
            self.clock = service.storage.metadata_get(_CLOCK + source_id)
            ref = numilog_task_reference()
            if ref is not None:
                self.refs["task_ref"] = ref
            self.refs["run_ref"] = secrets.token_hex(6)
        except Exception:
            pass

    def record_item(self, position, decision, *, item_id=None):
        """Emit exact raw purchase evidence; keep the bounded UI projection."""
        if (type(position) is not int or not 1 <= position <= 2048
                or type(decision) is not str or decision not in ITEM_DECISIONS):
            return
        # The caller passes only a uniquely validated ID from its authenticated
        # account manifest. A position alone is not stable across two runs.
        # Keep this observed identifier in the RAW journal, not in the
        # compact per-run GTK aggregate; no additional provider request.
        if (type(item_id) is str and 0 < len(item_id) <= 1024
                and not any(ch in item_id for ch in "\0\r\n")):
            _LOG.info(
                "TRACE numilog purchase decision provider_id=numilog "
                "source_id=%s item_id=%s position=%s decision=%s "
                "run_ref=%s task_ref=%s",
                self.source_id, item_id, position, decision,
                self.refs.get("run_ref"), self.refs.get("task_ref"),
            )
        if len(self.item_decisions) == 8:
            self.items_truncated = True
        else:
            # The structured journal serializes nested objects with sorted
            # JSON keys, whereas its closed human-readable companion uses
            # Python insertion order. Match both representations exactly.
            self.item_decisions.append({"decision": decision, "position": position})

    def fields(self, phase, code, status):
        cause_phase = self.cause[0] if self.cause else phase
        details = dict(
            item_decisions=list(self.item_decisions),
            items_truncated=self.items_truncated,
        ) if self.item_decisions else {}
        return dict(phase=phase, cause_phase=cause_phase, code=code, status=status,
                    task_kind="source-materialize", **self.counts, **details, **self.refs)

    def emit(self, phase, code, status="running"):
        fields = public_fields(self.fields(phase, code, status))
        if not fields:
            return {}
        # The SAME validated facts go to both channels. No exception, source
        # key or arbitrary plugin payload can be appended to this emission.
        # Logging remains independent of collector enablement/retention.
        try:
            # The original TRACE record remains unchanged for legacy handlers.
            # The Core formatter writes its validated fields as a structured
            # occurrence, not as a second event alongside the TRACE.
            from ..diagnostics import _core_journal_record
            serialized = json.dumps(fields, separators=(",", ":"), sort_keys=True)
            message = _LOG_PREFIX + serialized
            public_status = (
                "error" if fields["status"] in {"failed", "partial"}
                else "warning" if fields["status"] == "blocked" else "ok"
            )
            _severity = (
                logging.ERROR if fields["status"] == "failed"
                else logging.WARNING if fields["status"] in {"partial", "blocked"}
                else logging.INFO
            )
            _LOG.log(
                _severity, "%s%s", _LOG_PREFIX, serialized,
                extra=_core_journal_record(
                    "NUMILOG", "startup_materialization",
                    public_status, message, fields,
                ),
            )
        except Exception:
            pass  # an optional log handler cannot alter acquisition outcomes
        try:
            self.service.diagnostics.add_core_numilog_startup(fields)
        except Exception:
            pass
        try:
            if self.claimed or phase == "completed":
                save(self.service.storage, self.source_id, self.clock, fields, claimed=self.claimed)
        except Exception:
            pass  # optional evidence cannot alter acquisition or cancellation
        return fields

    def event(self, phase, code="started"):
        self.phase = phase
        self.emit(phase, code)

    def fail(self, code=None):
        if self.cause is None:
            self.cause = (self.phase, code or {
                "admission": "admission_failed", "durable_claim": "claim_failed",
                "manifest_fetch": "manifest_failed", "rights_mirror": "rights_mirror_failed",
                "preflight": "preflight_failed", "acquisition": "acquisition_failed",
                "adept": "adept_failed", "index": "index_failed",
                "receipt_finalization": "receipt_finalize_failed",
            }.get(self.phase, "interrupted"))

    def finish(self, status):
        if status == "not_due" and self.cause is None:
            self.cause = (self.phase, "not_due")
        return self.emit("completed", self.cause[1] if self.cause else
                         "not_due" if status == "not_due" else "complete", status)

    @contextmanager
    def acquisition(self):
        token = _ACTIVE.set(self)
        try:
            yield
        finally:
            _ACTIVE.reset(token)


_ACQUISITION_DENIAL_CODES = frozenset(code for code in CODES
    if code.startswith("acquisition_") and code != "acquisition_failed")


def acquisition_refusal(code):
    """Record only a closed failure boundary within the active startup run.

    This must never become an authorization, retry signal or logger of
    untrusted exception details. Manual acquisitions have no active trace.
    """
    trace = _ACTIVE.get()
    if trace is not None and code in _ACQUISITION_DENIAL_CODES:
        trace.fail(code)


@contextmanager
def adept_stage(provider_id):
    """Mark ADEPT only when the public Core import is actually entered."""
    trace = _ACTIVE.get() if provider_id == "numilog" else None
    if trace is not None:
        trace.event("adept")
    try:
        yield trace
    except Cancelled:
        if trace is not None:
            trace.fail("interrupted")
        raise
    except BaseException:
        if trace is not None:
            trace.fail("adept_failed")
        raise
    else:
        if trace is not None:
            trace.event("acquisition", "adept_complete" if trace.cause is None else "started")
