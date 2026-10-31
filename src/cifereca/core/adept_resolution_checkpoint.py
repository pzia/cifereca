"""Read-only checkpoint around an explicitly selected Paris/ADEPT preview.

An equality fingerprint is only a local diagnostic. In particular, the two
snapshot reads are NOT an atomic database transaction, do not inspect ebook
file bytes, and do not prove current Adobe/Paris server state.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from .adept_resolution_preview import preview_paris_adept_resolution_candidate
from .adept_resolution_snapshot import (
    snapshot_paris_adept_local_evidence,
)


def preview_paris_adept_with_local_checkpoint(
    storage: Any,
    *,
    loan_id: str,
    item_id: str,
    source_reference_id: str,
    acquisition_ref: str,
    artifact_inspector: Callable[[Path], Any],
) -> dict[str, Any]:
    """Qualify one local preview with a before/after evidence fingerprint.

    Fails closed if an invalid selection, unavailable evidence, or any
    observed change prevents a stable local review. It deliberately exposes
    neither a commit path nor return/fulfillment authorization.
    """
    args = (loan_id, item_id, source_reference_id, acquisition_ref)
    if not (
        all(
            type(value) is str and bool(value)
            and value == value.strip()
            and not any(c in value for c in ("\x00", "\r", "\n"))
            for value in args
        )
        and len(acquisition_ref) == 16
        and all(c in "0123456789abcdef" for c in acquisition_ref)
    ):
        return _blocked(
            "invalid_selection", "invalid_exact_reference",
        )

    before = snapshot_paris_adept_local_evidence(
        storage, loan_id=loan_id, item_id=item_id,
    )
    if before["status"] != "local_snapshot":
        return _blocked(
            "local_evidence_unavailable", "local_snapshot_unavailable",
        )

    candidate = preview_paris_adept_resolution_candidate(
        storage,
        loan_id=loan_id,
        item_id=item_id,
        source_reference_id=source_reference_id,
        acquisition_ref=acquisition_ref,
        artifact_inspector=artifact_inspector,
    )
    after = snapshot_paris_adept_local_evidence(
        storage, loan_id=loan_id, item_id=item_id,
    )
    if (after["status"] != "local_snapshot"
            or before["fingerprint"] != after["fingerprint"]):
        # Do not reveal any profile, acquisition or source from a stale
        # preview, even if the internal candidate had been reviewable.
        return _blocked(
            "local_evidence_changed_during_preview",
            "rerun_preview_after_local_evidence_change",
        )

    # Preserve the advisory status/reasons of the existing preview, including
    # all rejection paths. This hash is not a token or grant and must be
    # rechecked after another read; the snapshot does not lock other writers.
    return {
        **candidate,
        "local_evidence_fingerprint": before["fingerprint"],
        "local_evidence_algorithm": before["algorithm"],
        "local_evidence_status": "observed_unchanged_during_preview",
        "adobe_profile_resolution": "unknown",
        "return_authorized": False,
        "commit_available": False,
        "requires_explicit_review": True,
        "checkpoint_note": (
            "Empreinte locale seulement : les fichiers physiques, la preuve "
            "Adobe serveur et le prêt Paris actuel restent à vérifier. "
            "Une opération future exigera une relecture atomique."
        ),
    }


def _blocked(status: str, reason: str) -> dict[str, Any]:
    return {
        "status": status,
        "review_reasons": [reason],
        "source_reference_id": None,
        "acquisition_ref": None,
        "recorded_adept_profile_id": None,
        "recorded_adept_loan_ref": None,
        "historical_evidence": "unknown",
        "local_evidence_fingerprint": None,
        "local_evidence_algorithm": "sha256:paris-adept-local-evidence-v1",
        "local_evidence_status": "unavailable",
        "adobe_profile_resolution": "unknown",
        "return_authorized": False,
        "commit_available": False,
        "requires_explicit_review": True,
    }
