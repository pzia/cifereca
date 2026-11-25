"""Privacy-safe explanation of exact loan cover gaps (#6168, N0/N2).

A Library card's picture is not a borrower's entitlement or an OriginLink.
This diagnostic distinguishes a visible, *unlinked* candidate from an attested
loan cover without attaching that image to the loan or exposing title, URL,
account, credential, identity, filesystem path or provider responses.

The caller supplies the SAME bounded Core Library snapshot used for its
N1 resolution; no network, GTK or filesystem operation is performed.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from ..models import Loan
from .book_cover_projection import ResolvedBookCover
from .book_cover_source_projection import project_exact_loan_cover
from .covers import candidate


LoanGapReason = Literal[
    "source_unqualified", "partial_snapshot", "attested_embedded",
    "attested_origin", "supplier_catalogue_only", "conflicting_evidence",
    "visible_library_image_without_loan_proof", "no_admissible_evidence",
]


@dataclass(frozen=True, slots=True)
class LoanCoverGap:
    """A closed, identifier-free public diagnostic; decision is NOT exported."""

    status: Literal["selected", "missing", "pending", "ambiguous", "invalid"]
    reason: LoanGapReason
    library_candidate_observed: bool
    exact_loan_proof_selected: bool

    def public_fields(self) -> dict[str, object]:
        # All values are closed vocabulary or booleans, never raw evidence.
        return {
            "status": self.status, "reason": self.reason,
            "library_candidate_observed": self.library_candidate_observed,
            "exact_loan_proof_selected": self.exact_loan_proof_selected,
        }


def _visible_unlinked_library_hint(
    loan: Loan, rows: tuple[Mapping[str, object], ...],
) -> bool:
    """Only an *observational* provider/edition hint, never a documentary join.

    An exact loan/entitlement/Placement proof is still mandatory before any
    image can be published as a loan image. Avoid even counting a cover from
    another local profile. Do not use title, ISBN or media hash.
    """
    for row in rows:
        if (row.get("identity_id") != loan.identity_id
                or row.get("provider_id") != loan.source_provider
                or row.get("item_id") != loan.record_id):
            continue
        metadata = row.get("metadata")
        selected = (
            metadata.get("cover_resolution")
            if type(metadata) is dict else None
        )
        if (type(selected) is not dict
                or selected.get("status") != "selected"
                or selected.get("url") != row.get("thumbnail_url")):
            continue
        tier = selected.get("tier")
        if tier not in ("embedded", "origin", "catalog"):
            continue
        # A public URL or a locally attested EPUB image is merely observed.
        # The loan adapter does NOT consume this hint as cover evidence.
        if candidate(
            row.get("thumbnail_url"), tier=tier, source="library-observation",
        ) is not None:
            return True
    return False


def diagnose_exact_loan_cover(
    loan: Loan,
    library_snapshot: tuple[Mapping[str, object], ...], *,
    revision: int,
    snapshot_complete: bool,
) -> tuple[ResolvedBookCover | None, LoanCoverGap]:
    """Return N1 decision and a separate safe diagnostic from the same rows."""
    # Let Core N1 reject malformed/unbounded inputs without weakening admission.
    decision = project_exact_loan_cover(
        loan, library_snapshot, revision=revision,
        snapshot_complete=snapshot_complete,
    )
    return decision, describe_loan_cover_gap(loan, library_snapshot, decision)


def describe_loan_cover_gap(
    loan: Loan, library_snapshot: tuple[Mapping[str, object], ...],
    decision: ResolvedBookCover | None,
) -> LoanCoverGap:
    """Explain an already admitted N1 result without another N1 resolution.

    The CoreUI caller must pass the exact snapshot from that decision.
    The hint is diagnostic only and never changes a decision or a right.
    """
    if decision is None:
        return LoanCoverGap("invalid", "source_unqualified", False, False)
    if decision.status == "pending":
        return LoanCoverGap(
            "pending", "partial_snapshot", False, False,
        )
    if decision.status == "ambiguous":
        return LoanCoverGap(
            "ambiguous", "conflicting_evidence", False, False,
        )
    # This hint is not an attestation and must never affect N1's outcome.
    hint = _visible_unlinked_library_hint(loan, library_snapshot)
    if decision.status == "missing":
        return LoanCoverGap(
            "missing",
            ("visible_library_image_without_loan_proof"
             if hint else "no_admissible_evidence"),
            hint, False,
        )
    if decision.tier == "embedded":
        reason: LoanGapReason = "attested_embedded"
    elif decision.tier == "origin":
        reason = "attested_origin"
    else:
        reason = "supplier_catalogue_only"
    return LoanCoverGap(
        "selected", reason, hint, decision.tier in ("embedded", "origin"),
    )


__all__ = ["LoanCoverGap", "diagnose_exact_loan_cover", "describe_loan_cover_gap"]
