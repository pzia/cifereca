"""P0 #3909: one-run, opaque, fail-closed Core delivery phase diagnostics.

This is a logging envelope, NOT evidence or admission. Never serialize a
SourceKey, offer ID, Account, URL, filename, digest, token or exception.
The HMAC key is random per executor invocation and never leaves memory.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets

from .source_convergence import SourceKey

_LOG = logging.getLogger("cifereca")
_PHASES = frozenset({
    "observed", "presence", "stock_recovery", "recovery", "delivery", "receipt",
    "promotion", "publication", "terminal",
})
_STEPS = frozenset({"start", "ok", "miss", "failed"})
_TERMINAL = frozenset({
    "downloaded", "recovered", "present_attested", "pending", "blocked",
    "failed", "cancelled", "skipped_archived", "inactive",
})
_REASONS = frozenset({
    "cancelled", "scope_rebound", "exact_verified_origin",
    "canonical_stock_relocated", "offer_unconfirmed", "delivery_archived",
    "right_inactive", "delivery_unconfirmed", "consent_unconfigured",
    "cancelled_after_get", "scope_rebound_after_get",
    "consent_revoked_after_get", "cancelled_after_receipt",
    "scope_or_consent_after_receipt", "cancelled_after_promotion",
    "scope_or_consent_after_promotion", "canonical_delivery_recovered",
    "canonical_delivery_committed", "delivery_cancelled",
    "canonical_promotion_failed", "delivery_or_commit_failed",
    "stock_origin_unproven", "stock_receipt_unproven",
    "stock_account_unbound", "stock_consent_changed",
    "stock_placement_unverified", "stock_bytes_changed",
    "stock_catalog_conflict", "stock_destination_unsafe",
    "stock_destination_conflict", "canonical_stock_repair_deferred",
})


class CanonicalFlowTrace:
    """Public-safe TRACE correlation; neither persistent nor reversible.

    A different random key per Sync makes a right's opaque reference unusable
    across runs. Only fixed phases/statuses and allowlisted reasons get logged.
    Log subsystem failure cannot prevent a Core acquisition or B commit.
    """

    def __init__(self) -> None:
        self.run_ref = secrets.token_hex(12)
        self._salt = secrets.token_bytes(32)

    def item_ref(self, key: SourceKey) -> str:
        if type(key) is not SourceKey:
            raise ValueError("canonical_flow_source_required")
        scope = key.scope
        # JSON disambiguates tuple boundaries. No serialized coordinate is
        # logged; low-entropy resource IDs cannot be dictionary-attacked
        # without this unexposed, per-run salt.
        coordinates = json.dumps([
            scope.identity_id, scope.provider_id, scope.credential_id,
            scope.account_id, key.branch, key.edition_id, key.right_id,
            key.kind,
        ], ensure_ascii=True, separators=(",", ":")).encode("ascii")
        return hmac.new(self._salt, coordinates, hashlib.sha256).hexdigest()[:24]

    def emit(self, key: SourceKey, phase: str, code: str, *,
             reason: str = "") -> None:
        try:
            if (type(key) is not SourceKey
                    or type(phase) is not str or phase not in _PHASES
                    or type(code) is not str
                    or code not in (_TERMINAL if phase == "terminal" else _STEPS)
                    or type(reason) is not str):
                return
            # Only terminal verdicts have semantic reasons. Refuse any
            # unrecognized text rather than returning a provider exception.
            safe_reason = reason if (
                phase == "terminal" and reason in _REASONS
            ) else "none"
            _LOG.info(
                "TRACE canonical_flow run_ref=%s item_ref=%s phase=%s "
                "code=%s reason=%s kind=%s",
                self.run_ref, self.item_ref(key), phase, code,
                safe_reason, key.kind,
            )
        except Exception:
            # Diagnostic output has no right to affect delivery or persistence.
            pass


__all__ = ["CanonicalFlowTrace"]
