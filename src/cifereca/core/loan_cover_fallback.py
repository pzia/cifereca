"""Exact, read-only local-cover fallback for a loan with no thumbnail.

The library projection must attest an actual identity-owned Placement and an
exact source-bound loan/entitlement link. A title, ISBN, bare record ID or
content SHA alone never establishes this relationship. No I/O is performed.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping

from ..models import Loan
from .covers import CoverResolution, candidate, resolve_cover
from .loan_source_identity import loan_source_key


def loan_local_cover(
    loan: Loan, library_rows: Iterable[Mapping[str, object]],
) -> CoverResolution:
    """Resolve only attested covers of *this* borrower and loan.

    An absent/unknown borrower account is not interchangeable with the
    explicit empty Paris primary-account ID. Different verified local
    artifacts with equally strong, conflicting covers stay ambiguous.
    """
    missing = resolve_cover(())
    if (type(loan) is not Loan
            or type(loan.identity_id) is not str or not loan.identity_id
            or type(loan.source_provider) is not str or not loan.source_provider
            or type(loan.source_credential) is not str or not loan.source_credential
            or type(loan.source_account_id) is not str
            or type(loan.record_id) is not str or not loan.record_id
            or type(loan.id) is not str or not loan.id):
        return missing

    scope = loan_source_key(loan)
    candidates = []
    for row in library_rows:
        if (type(row) is not dict or row.get("local") is not True
                or row.get("identity_id") != loan.identity_id
                or type(row.get("placement_id")) is not str
                or not row["placement_id"]
                or type(row.get("artifact_id")) is not str
                or not row["artifact_id"]):
            continue
        proofs = row.get("_exact_loan_cover_proofs", ())
        if type(proofs) not in (tuple, list) or len(proofs) > 128:
            continue
        for proof in proofs:
            if (type(proof) is not dict
                    or proof.get("loan_source_key") != scope
                    or proof.get("identity_id") != loan.identity_id
                    or proof.get("source_provider") != loan.source_provider
                    or proof.get("source_credential") != loan.source_credential
                    or proof.get("source_account_id") != loan.source_account_id
                    or proof.get("record_id") != loan.record_id
                    or proof.get("loan_id") != loan.id
                    or proof.get("artifact_id") != row["artifact_id"]
                    or proof.get("placement_id") != row["placement_id"]):
                continue
            tier = proof.get("tier")
            if tier not in ("embedded", "origin", "catalog"):
                continue
            # Equal-strength conflicting evidence must remain ambiguous.
            cover = candidate(
                proof.get("url"), tier=tier, source="verified-local-placement",
                evidence_id=str(row["placement_id"]), evidence_strength=100,
            )
            if cover is not None:
                candidates.append(cover)
    return resolve_cover(candidates)


__all__ = ["loan_local_cover"]
