"""Pure successful digital-loan acquisition receipt projection (#1120 P0).

Keep the existing storage/public API contract and preserve legacy nullable
metadata. No loan return, Adobe runtime, retry, filesystem mutation, or
authorisation lives here; the caller retains their ordering and guards.
"""
from __future__ import annotations

from typing import Any


def completed_receipt(
    *, ledger_base: dict, scan: dict, acquisition_kind: str,
    final_path: Any, loan: Any, force_refresh: bool, decision: Any,
    previous_path: Any, adept_binding: Any, artifact_drm: Any,
    artifact_format: Any, artifact_protected: Any,
    hook_info: Any, final_inspection: Any,
) -> dict:
    """Exactly the historical successful acquisition_put mapping."""
    is_acsm = acquisition_kind == "acsm"
    return {
        **ledger_base, "status": "complete", "strategy": "loan_auto",
        "artifact_id": scan.get("artifact_id"),
        "executor": "kobo-adept" if is_acsm else "provider",
        "metadata": {
            "path": str(final_path),
            "catalog_index": scan,
            "provider_loan_id": loan.id,
            "reacquisition": force_refresh,
            "acquisition_decision": decision.as_dict(),
            "preserved_artifact_path": (
                str(previous_path)
                if force_refresh and previous_path and previous_path.is_file()
                else None
            ),
            "adept_binding": adept_binding if is_acsm else None,
            "post_processed": True if is_acsm else None,
            "adept_artifact_drm": artifact_drm if is_acsm else None,
            "artifact_format": artifact_format or final_inspection.media_format,
            "artifact_protected": (
                artifact_protected if is_acsm else final_inspection.protected
            ),
            "hook_reported": bool(hook_info) if is_acsm else None,
        },
    }
