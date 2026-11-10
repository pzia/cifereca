"""Closed, optional startup telemetry. Never an acquisition authority (#2131)."""
from __future__ import annotations

from contextlib import contextmanager
import json
import logging
import re

from ..diagnostic_task_context import numilog_task_reference
from ..plugin_api import Cancelled

PHASES = frozenset({"admission", "durable_claim", "manifest_fetch", "rights_mirror",
    "preflight", "acquisition", "adept", "index", "receipt_finalization", "completed"})
CODES = frozenset({"started", "complete", "not_due", "consent_revoked", "admission_failed",
    "claim_failed", "manifest_failed", "manifest_invalid", "rights_mirror_failed",
    "preflight_failed", "rights_mirror_incomplete", "acquisition_failed", "adept_failed", "index_failed",
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


@contextmanager
def adept_stage(provider_id):
    """Compatibility context for public ACSM imports; no retired startup worker."""
    yield None
