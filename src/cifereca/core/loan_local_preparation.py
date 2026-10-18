"""Local automatic-loan preparation under the facade's existing per-loan locks.

Orchestrate catalog validation/invalidation and relinking before reuse/retry.
The loan_artifact_state classifiers remain read-only; mutations here retain
historical order. No provider or ADEPT operation belongs to this phase.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any
from . import loan_artifact_state
from .loan_safe_trace import loan_trace_ref


@dataclass(frozen=True, slots=True, repr=False)
class LocalLoanPreparation:
    previous_state: loan_artifact_state.PreviousLoanArtifact
    decision: Any
    outcome: dict[str, Any] | None = None


def prepare(self, loan, *, identity, entitlement_id, force_refresh, started,
            inspect_artifact, decide_artifact_reuse, blocked_automatic_adept_retry,
            time, _TRACE_LOG) -> LocalLoanPreparation:
    trace_ref = loan_trace_ref(loan, identity.identity_id)
    existing = loan_artifact_state.local_catalog_candidates(
        self.storage.catalog_entries(),
        identity_id=identity.identity_id,
        source_provider=loan.source_provider,
        item_id=str(loan.record_id or loan.id),
    )
    # Inventory is a projection. Validate its files before deciding to skip.
    # Keep invalidation writes in the same candidate order and under
    # the existing per-loan locks. Classifier helpers remain read-only.
    valid_existing = []
    for entry in existing:
        usable, path = loan_artifact_state.inspect_catalog_local_file(
            entry, artifact_inspector=inspect_artifact,
        )
        if usable:
            valid_existing.append(entry)
            continue
        self.storage.catalog_entry_put({**entry, "local_state": "missing" if path is None or not path.is_file() else "invalid"})
        _TRACE_LOG.info("TRACE download local-recheck loan_ref=%s catalog_state=%s", trace_ref, "missing" if path is None or not path.is_file() else "invalid")
    existing = valid_existing
    previous_state = loan_artifact_state.inspect_previous_loan_artifact(
        self.storage.acquisitions(provider_id=loan.source_provider),
        identity_id=identity.identity_id,
        credential_id=loan.source_credential,
        account_id=loan.source_account_id,
        item_id=str(loan.record_id or loan.id),
        source_provider=loan.source_provider,
        artifact_inspector=inspect_artifact,
    )
    previous = previous_state.row
    previous_meta = previous_state.metadata
    previous_path = previous_state.path
    previous_inspection = previous_state.inspection
    legacy_acsm_artifact = previous_state.legacy_acsm_artifact
    previous_complete_local = previous_state.complete_local
    if previous_complete_local and not existing and not force_refresh:
        repaired_index = self.catalog_index_path(
            previous_path, title=loan.title, item_id=str(loan.record_id or loan.id),
            identity_id=identity.identity_id, provider_id=loan.source_provider,
        )
        _TRACE_LOG.info(
            "TRACE download loan_auto catalog-relink loan_ref=%s indexed=%s",
            trace_ref, repaired_index.get("status") == "indexed",
        )
        existing = [repaired_index] if repaired_index.get("status") == "indexed" else existing
    legacy_reason = (
        "artifact_validation"
        if legacy_acsm_artifact and previous_meta.get("post_processed")
        else "missing_post_process_marker"
        if legacy_acsm_artifact
        else ""
    )
    decision = decide_artifact_reuse(
        force_refresh=force_refresh,
        catalog_valid=bool(existing),
        ledger_complete_valid=previous_complete_local,
        legacy_repair=legacy_acsm_artifact,
        legacy_reason=legacy_reason,
        previous_exists=previous is not None,
        previous_path_exists=bool(previous_path is not None and previous_path.is_file()),
        previous_valid=(previous_inspection.valid if previous_inspection is not None else None),
        previous_protected=(previous_inspection.protected if previous_inspection is not None else None),
    )
    _TRACE_LOG.info(
        "TRACE download loan_auto decision loan_ref=%s action=%s reason=%s source=%s catalog_valid=%s ledger_valid=%s force_refresh=%s dt=%.3fs",
        trace_ref, decision.action, decision.reason, decision.source,
        bool(existing), previous_complete_local, force_refresh, time.monotonic()-started,
    )
    if decision.action == "reuse":
        _TRACE_LOG.info(
            "TRACE download loan_auto skip-local loan_ref=%s source=%s reason=%s dt=%.3fs",
            trace_ref, decision.source, decision.reason, time.monotonic()-started,
        )
        return LocalLoanPreparation(previous_state, decision, {
            "status": "unchanged",
            "loan_id": loan.id,
            "decision": decision.action,
            "reason": decision.reason,
            "source": decision.source,
        })
    if blocked_automatic_adept_retry(
        previous, provider_loan_id=loan.id, entitlement_id=entitlement_id,
        force_refresh=force_refresh,
    ):
        # The existing ledger is the authoritative pending-intervention
        # state. Preserve diagnostic, ACSM staging and any prior artifact;
        # do not acquire the provider link or attempt a different profile.
        code = str((previous or {}).get("attention_code")
                   or (previous or {}).get("error_code") or "adept_attention_required")
        _TRACE_LOG.warning(
            "TRACE download loan_auto auto-retry-blocked loan_ref=%s "
            "reason=persisted_adept_attention",
            trace_ref,
        )
        return LocalLoanPreparation(previous_state, decision, {
            "status": "attention_required", "loan_id": loan.id,
            "code": code, "reason": "persisted_adept_attention",
            "retry": "explicit_only",
        })

    if decision.action == "repair_legacy":
        _TRACE_LOG.info(
            "TRACE download loan_auto repair-legacy loan_ref=%s reason=%s",
            trace_ref, decision.reason,
        )
    return LocalLoanPreparation(previous_state, decision)
