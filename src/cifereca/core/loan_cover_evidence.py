"""Loan -> local EPUB cover handoff using an attested Core entitlement link.

The borrower-scoped loan SourceKey and the owner's Artifact+Placement MUST be
joined by Core's already materialized `_exact_loan_cover_proofs`; a matching
title, record_id, ISBN, content digest or cached N3 image is never a join key.
This is a pure, bounded projection. It performs no provider/disk/EPUB I/O.
"""
from __future__ import annotations

from collections.abc import Mapping

from ..models import Loan
from .book_cover_source_projection import exact_loan_cover_scope
from .covers import candidate
from .local_cover_evidence import VerifiedLocalCoverEvidence


def exact_attested_loan_epub_cover(
    loan: Loan,
    library_rows: tuple[Mapping[str, object], ...],
) -> VerifiedLocalCoverEvidence | None:
    """Return one local EPUB packet only when the *loan* proves its Placement.

    An attested loan-to-Placement link does not partition N3 image bytes by
    profile: it only authorizes N2 to reuse a source under that owner. The
    physical EPUB image may be missing; the immutable Core packet still wins
    N1 over an origin or catalogue URL.
    """
    scope = exact_loan_cover_scope(loan)
    if (scope is None or type(library_rows) is not tuple
            or len(library_rows) > 10000):
        return None
    selected: VerifiedLocalCoverEvidence | None = None
    for row in library_rows:
        if (type(row) is not dict
                or row.get("local") is not True
                or row.get("identity_id") != scope.identity_id
                or row.get("_library_view_owner") not in (None, scope.identity_id)):
            continue
        facts = row.get("_exact_local_cover_evidence")
        if (type(facts) is not VerifiedLocalCoverEvidence
                or facts.embedded is None
                or facts.scope.kind != "placement"
                or facts.scope.identity_id != scope.identity_id
                or facts.scope.source_key != row.get("key")
                or facts.scope.artifact_id != row.get("artifact_id")
                or facts.scope.placement_id != row.get("placement_id")):
            continue
        # The final merged Library row may have a newer descriptive
        # thumbnail from another entitlement. Its mutable current URL is
        # *not* the loan->Placement authorization. Core emitted the private
        # immutable proof when the exact entitlement was resolved.
        proofs = row.get("_exact_loan_cover_proofs")
        if type(proofs) not in (tuple, list) or len(proofs) > 128:
            continue
        admitted = any(
            type(proof) is dict
            and proof.get("loan_source_key") == scope.source_key
            and proof.get("identity_id") == scope.identity_id
            and proof.get("source_provider") == scope.provider_id
            and proof.get("source_credential") == scope.credential_id
            and proof.get("source_account_id") == scope.account_id
            and proof.get("loan_id") == scope.loan_id
            and proof.get("record_id") == loan.record_id
            and proof.get("artifact_id") == facts.scope.artifact_id
            and proof.get("placement_id") == facts.scope.placement_id
            and proof.get("tier") in ("embedded", "origin", "catalog")
            and candidate(
                proof.get("url"), tier=proof.get("tier"),
                source="verified-local-placement",
            ) is not None
            for proof in proofs
        )
        if not admitted:
            continue
        if selected is not None and selected != facts:
            # Two different local Placements claimed by this same loan:
            # no first/last-wins display authorization.
            return None
        selected = facts
    return selected


__all__ = ["exact_attested_loan_epub_cover"]
