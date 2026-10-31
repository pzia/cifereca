"""Explicit, read-only candidate selection for exceptional Paris/ADEPT origins.

This is the PREVIEW phase of human reconciliation, NOT a fulfillment/return
resolver. It never persists a choice or grants authority. Selecting an
acquisition cannot erase conflicting evidence from another acquisition.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from .adept_canonical_origin_review import review_paris_canonical_adept_links
from .adept_origin_audit import _opaque_ref, inspect_paris_adept_origins


def preview_paris_adept_resolution_candidate(
    storage: Any,
    *,
    loan_id: str,
    item_id: str,
    source_reference_id: str,
    acquisition_ref: str,
    artifact_inspector: Callable[[Path], Any],
) -> dict[str, Any]:
    """Preview an explicitly named local source and historical acquisition.

    The two refs are *not* permission tokens. The caller must select them
    from independently displayed local diagnostic reports. The candidate
    cannot become an Adobe return authority, even when every local witness
    agrees. No credentials or raw ADEPT loan tokens are returned.
    """
    args = (loan_id, item_id, source_reference_id, acquisition_ref)
    valid = all(
        type(value) is str and value.strip() and value == value.strip()
        and "\x00" not in value and "\r" not in value and "\n" not in value
        for value in args
    ) and len(acquisition_ref) == 16 and all(
        char in "0123456789abcdef" for char in acquisition_ref
    )
    def result(status: str, reasons: list[str], *, selected=None,
               canonical_source=None, strength="unknown") -> dict[str, Any]:
        return {
            "status": status,
            "review_reasons": reasons,
            "source_reference_id": (
                canonical_source["source_reference_id"]
                if canonical_source is not None else None
            ),
            "acquisition_ref": (
                selected["acquisition_ref"] if selected is not None else None
            ),
            "recorded_adept_profile_id": (
                selected["recorded_adept_profile_id"]
                if selected is not None else None
            ),
            "recorded_adept_loan_ref": (
                selected["recorded_adept_loan_ref"]
                if selected is not None else None
            ),
            "historical_evidence": strength,
            "adobe_profile_resolution": "unknown",
            "return_authorized": False,
            "commit_available": False,
            "requires_explicit_review": True,
            "note": (
                "Sélection locale de preuve seulement ; ce résultat ne "
                "certifie ni le profil ayant réellement activé la licence "
                "ni une permission de retour Adobe ou Paris."
            ),
        }

    if not valid:
        return result("invalid_selection", ["invalid_exact_reference"])

    historical = inspect_paris_adept_origins(
        loan_id=loan_id, item_id=item_id,
        entitlements=storage.entitlements(provider_id="paris"),
        acquisitions=storage.acquisitions(provider_id="paris"),
        artifact_inspector=artifact_inspector,
    )
    canonical = review_paris_canonical_adept_links(
        storage, loan_id=loan_id, item_id=item_id,
    )
    sources = [
        row for row in canonical["sources"]
        if row["source_reference_id"] == source_reference_id
    ]
    origins = [
        row for row in historical["origins"]
        if row["acquisition_ref"] == acquisition_ref
    ]
    if len(sources) != 1 or not origins:
        return result("selection_missing", ["exact_source_or_acquisition_missing"])
    source = sources[0]
    if len(origins) != 1:
        # An old acquisition without a persisted unique ID can hash to
        # the same ref multiple times; never pick one arbitrarily.
        return result("ambiguous_acquisition", ["historical_acquisition_ref_reused"],
                      canonical_source=source)
    selected = origins[0]
    strength = selected["evidence"]

    if (historical["origin_resolution"] == "conflicting_records"
            or canonical["link_resolution"] == "conflicting_records"):
        return result("conflicting_records", ["independent_origin_evidence_conflicts"],
                      selected=selected, canonical_source=source, strength=strength)

    expected_ref = _opaque_ref(
        "paris", source["library_identity_id"], source["credential_id"],
        source["account_id"],
    )
    if selected["source_ref"] != expected_ref:
        # Same title, SHA, or borrower seen in another local library does not
        # prove which Adobe account activated the exact Paris transaction.
        return result("source_scope_mismatch", ["acquisition_not_in_selected_library_scope"],
                      selected=selected, canonical_source=source, strength=strength)

    if (selected["source_visibility_evidence"] != "persisted_entitlement"
            or source["source_state"] != "active"):
        return result("source_unverified", ["exact_paris_entitlement_unverified"],
                      selected=selected, canonical_source=source, strength=strength)

    if (canonical["link_resolution"] != "paired_links_unverified"
            or len(source["artifacts"]) != 1
            or not source["artifacts"][0]["paired_source_artifact_links"]
            or source["cross_identity_link"]):
        return result("canonical_links_unverified", ["canonical_origin_pair_unverified"],
                      selected=selected, canonical_source=source, strength=strength)

    if (historical["origin_resolution"] != "single_inventory_attested"
            or strength != "inventory_attested"
            or not selected["recorded_adept_profile_id"]
            or not selected["recorded_adept_loan_ref"]):
        return result("historical_inventory_unverified",
                      ["exact_adobe_activation_not_attested"],
                      selected=selected, canonical_source=source, strength=strength)

    # Strongest *local* reviewable candidate, not server validation. A human
    # must still reconcile the exact Adobe transaction independently.
    return result("review_candidate", ["server_transaction_attestation_required"],
                  selected=selected, canonical_source=source, strength=strength)
