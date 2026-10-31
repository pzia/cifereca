"""Read-only reconciliation between historical Paris/ADEPT and canonical v14 evidence.

A legacy entitlement has no canonical branch identifier, and a v14 ADEPT
OriginLink does not by itself record the Adobe activation profile. Therefore
a matching Paris credential/account/identity is *only a local scope overlap*,
not evidence that either record is the same Adobe fulfillment transaction.

This module never creates grants, updates the ledger, contacts a provider,
selects an ADEPT profile, replays an ACSM or authorizes a return.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any, Callable

from .adept_canonical_origin_review import review_paris_canonical_adept_links
from .adept_origin_audit import _opaque_ref, inspect_paris_adept_origins


def review_paris_adept_transaction(
    storage: Any,
    *,
    loan_id: str,
    item_id: str,
    artifact_inspector: Callable[[Path], Any],
) -> dict[str, Any]:
    """Compare existing independent audits by exact Paris account scope only.

    The two source stores are *not* joined by filename, title, ISBN or SHA.
    A shared account scope never proves a branch or an Adobe loan reference:
    even the strongest successful comparison remains advisory-only.
    """
    valid = (
        type(loan_id) is str and type(item_id) is str
        and bool(loan_id.strip()) and bool(item_id.strip())
        and "\x00" not in loan_id and "\x00" not in item_id
    )
    if not valid:
        return {
            "status": "unknown", "review_reasons": ["invalid_exact_scope"],
            "sources": [], "historical_unmatched_source_count": 0,
            "historical_unmatched_acquisition_count": 0,
            "adobe_profile_resolution": "unknown", "return_authorized": False,
            "requires_explicit_review": True,
        }
    loan_id, item_id = loan_id.strip(), item_id.strip()

    # Distinct observers, neither authorizes the other. In particular a
    # missing v14 source must not be backfilled from an old loan receipt.
    historical = inspect_paris_adept_origins(
        loan_id=loan_id, item_id=item_id,
        entitlements=storage.entitlements(provider_id="paris"),
        acquisitions=storage.acquisitions(provider_id="paris"),
        artifact_inspector=artifact_inspector,
    )
    canonical = review_paris_canonical_adept_links(
        storage, loan_id=loan_id, item_id=item_id,
    )
    seen_historical_rights = {
        row["source_ref"] for row in historical["sources"]
    }
    historical_origins: Counter[str] = Counter(
        row["source_ref"] for row in historical["origins"]
    )
    canonical_scopes = Counter(
        (row["library_identity_id"], row["credential_id"], row["account_id"])
        for row in canonical["sources"]
    )
    matched_refs: set[str] = set()
    sources = []
    for source in canonical["sources"]:
        scope = (
            source["library_identity_id"], source["credential_id"],
            source["account_id"],
        )
        historical_ref = _opaque_ref("paris", *scope)
        exact_right = historical_ref in seen_historical_rights
        history_count = historical_origins.get(historical_ref, 0)
        if exact_right or history_count:
            matched_refs.add(historical_ref)
        # Multiple v14 branches under one legacy scope cannot be resolved
        # because the historical entitlement has no branch coordinate.
        if canonical_scopes[scope] > 1 and (exact_right or history_count):
            overlap = "ambiguous_canonical_branch"
        elif exact_right:
            overlap = "persisted_entitlement_scope_only"
        elif history_count:
            overlap = "acquisition_only_scope"
        else:
            overlap = "no_historical_scope"
        sources.append({
            "source_reference_id": source["source_reference_id"],
            "library_identity_id": scope[0],
            "credential_id": scope[1],
            "account_id": scope[2],
            "branch_id": source["branch_id"],
            "canonical_source_state": source["source_state"],
            "legacy_scope_overlap": overlap,
            "historical_entitlement_present": exact_right,
            "historical_acquisition_count": history_count,
            "canonical_artifact_count": len(source["artifacts"]),
            "canonical_link_pairs_unverified": sum(
                1 for row in source["artifacts"]
                if row["paired_source_artifact_links"]
            ),
        })

    unmatched_sources = seen_historical_rights - matched_refs
    unmatched_origins = sum(
        count for ref, count in historical_origins.items()
        if ref not in matched_refs
    )
    reasons: list[str] = []
    if not canonical["sources"]:
        reasons.append("canonical_exact_source_missing")
    if not historical["sources"]:
        reasons.append("historical_entitlement_missing")
    if not historical["origins"]:
        reasons.append("historical_adept_acquisition_missing")
    if historical["origin_resolution"] == "conflicting_records":
        reasons.append("historical_adobe_claims_conflict")
    if canonical["link_resolution"] == "conflicting_records":
        reasons.append("canonical_origin_links_conflict")
    if any(row["legacy_scope_overlap"] == "ambiguous_canonical_branch"
           for row in sources):
        reasons.append("historical_branch_unresolvable")
    if unmatched_sources:
        reasons.append("historical_entitlement_without_canonical_source")
    if unmatched_origins:
        reasons.append("historical_acquisition_without_canonical_source")
    if any(row["canonical_source_state"] != "active" for row in sources):
        # An inactive or unknown source is not necessarily revoked; do not
        # turn historical state into a live negative provider observation.
        reasons.append("canonical_source_not_attested_active")
    if any(row["legacy_scope_overlap"] == "acquisition_only_scope" for row in sources):
        reasons.append("historical_acquisition_without_entitlement")
    if any(row["legacy_scope_overlap"] == "no_historical_scope" for row in sources):
        reasons.append("canonical_source_without_historical_scope")
    if canonical["link_resolution"] in ("unknown", "unpaired_or_missing_links"):
        reasons.append("canonical_origin_links_incomplete")

    conflicting = any(reason in reasons for reason in (
        "historical_adobe_claims_conflict", "canonical_origin_links_conflict",
        "historical_branch_unresolvable",
    ))
    status = (
        "conflicting_records" if conflicting else
        "evidence_gap" if reasons else
        "scope_overlap_only"
    )
    return {
        "status": status,
        "loan_id": loan_id, "item_id": item_id,
        "canonical_link_resolution": canonical["link_resolution"],
        "historical_origin_resolution": historical["origin_resolution"],
        "historical_inventory_status": historical["status"],
        "review_reasons": reasons,
        "sources": sources,
        "historical_unmatched_source_count": len(unmatched_sources),
        "historical_unmatched_acquisition_count": unmatched_origins,
        # This cannot progress to a binding/return decision without explicit
        # Adobe inventory evidence tied to the *exact* transaction.
        "adobe_profile_resolution": "unknown",
        "return_authorized": False,
        "requires_explicit_review": True,
        "next_read_only_review": "compare_exact_adobe_receipt_and_inventory",
        "note": (
            "Un recouvrement exact des scopes Paris est un indice local, "
            "pas une preuve de la branche historique, du profil Adobe "
            "réellement activé, ni de l'autorisation de restitution."
        ),
    }
