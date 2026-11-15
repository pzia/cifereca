"""Read-only exact-source producers for the N1 cover contract (#6168).

The library producer consumes an ALREADY-VERIFIED local Core Placement row;
it never guesses an Artifact from an ISBN/title/SHA or opens a file.

The loan producer combines the original provider thumbnail with ONLY the
borrower-scoped proofs admitted by loan_cover_fallback. It never promotes a
Library image to an unrelated Loan. An incomplete library snapshot produces
pending, not a false negative. Account/entitlement permissions are unchanged.

These producers do not install GUI listeners or a cache. A caller must submit
the returned value to N2 under the same exact scope/revision and then N4
subscribe its book row and detail to the same decision.
"""
from __future__ import annotations

from collections.abc import Mapping

from ..models import Loan
from .book_cover_projection import (
    CoverDocumentScope, ResolvedBookCover, resolve_book_cover,
)
from .covers import CoverCandidate, candidate
from .loan_cover_fallback import loan_local_cover
from .loan_source_identity import loan_source_key


def _exact(value: object, *, allow_empty: bool = False) -> bool:
    return (
        type(value) is str
        and (allow_empty or bool(value))
        and len(value) <= 512
        and value.isprintable()
    )


def project_exact_loan_cover(
    loan: Loan, library_snapshot: tuple[Mapping[str, object], ...], *,
    revision: int, snapshot_complete: bool,
) -> ResolvedBookCover | None:
    """One normalised loan, one exact borrower, no side-effect or loose join.

    The caller passes the already materialised/complete bounded Library
    snapshot, not an unbounded generator; `complete=False` covers failed or
    partial reads and must not turn a missing local fallback into certainty.
    """
    if (type(loan) is not Loan
            or not _exact(loan.identity_id)
            or not _exact(loan.source_provider)
            or not _exact(loan.source_credential)
            or not _exact(loan.source_account_id, allow_empty=True)
            or not _exact(loan.id)
            or not _exact(loan.record_id)):
        return None
    if (type(library_snapshot) is not tuple or len(library_snapshot) > 10000
            or any(type(row) is not dict for row in library_snapshot)):
        raise TypeError("loan cover needs a bounded, materialized Core snapshot")
    scope = CoverDocumentScope(
        kind="loan", identity_id=loan.identity_id,
        source_key=loan_source_key(loan),
        provider_id=loan.source_provider,
        credential_id=loan.source_credential,
        account_id=loan.source_account_id, loan_id=loan.id,
    )
    if not snapshot_complete:
        return resolve_book_cover(
            scope, (), revision=revision, snapshot_complete=False,
        )

    proofs: list[CoverCandidate] = []
    provider_cover = candidate(
        loan.thumbnail_url, tier="catalog", source=loan.source_provider,
        evidence_id=loan.id, evidence_strength=0,
    )
    if provider_cover is not None:
        proofs.append(provider_cover)
    local = loan_local_cover(loan, library_snapshot)
    if local.status in ("selected", "ambiguous"):
        # The existing Core gate already checked SourceKey, Identity,
        # Credential, borrower, exact Loan/Entitlement and local Placement.
        # Preserve ALL admitted ties, including ambiguous origins.
        proofs.extend(local.candidates)
    return resolve_book_cover(
        scope, proofs, revision=revision, snapshot_complete=True,
        attested_book_identity=scope.source_key,
    )


def project_verified_local_library_cover(
    row: Mapping[str, object], *, revision: int, snapshot_complete: bool,
) -> ResolvedBookCover | None:
    """Only an already projected, exact local Core Artifact/Placement.

    A bare book_id, local_path, title, ISBN or common hash cannot substitute
    for both Artifact and Placement. No plugin-supplied arbitrary URL is
    upgraded to evidence without an admitted Core cover_resolution matching
    the row's selected thumbnail.
    """
    if (type(row) is not dict or row.get("local") is not True
            or not _exact(row.get("identity_id"))
            or not _exact(row.get("key"))
            or not _exact(row.get("artifact_id"))
            or not _exact(row.get("placement_id"))):
        return None
    scope = CoverDocumentScope(
        kind="placement",
        identity_id=row["identity_id"], source_key=row["key"],
        artifact_id=row["artifact_id"], placement_id=row["placement_id"],
    )
    if not snapshot_complete:
        return resolve_book_cover(
            scope, (), revision=revision, snapshot_complete=False,
        )
    metadata = row.get("metadata")
    resolution = metadata.get("cover_resolution") if type(metadata) is dict else None
    if type(resolution) is not dict:
        return None  # No authoritative completed Core cover projection.
    status = resolution.get("status")
    if status == "ambiguous" and row.get("thumbnail_url") in (None, ""):
        return ResolvedBookCover(
            scope, revision, "ambiguous", "conflicting_evidence",
        )
    if status == "missing" and row.get("thumbnail_url") in (None, ""):
        return resolve_book_cover(scope, (), revision=revision, snapshot_complete=True)
    if (status != "selected" or resolution.get("url") != row.get("thumbnail_url")):
        return None
    tier = resolution.get("tier")
    if tier not in ("embedded", "origin", "catalog"):
        return None
    selected = candidate(
        resolution.get("url"), tier=tier,
        source=resolution.get("source") or "verified-local-placement",
        evidence_id=resolution.get("evidence_id") or "",
    )
    if selected is None:
        return None
    return resolve_book_cover(
        scope, (selected,), revision=revision, snapshot_complete=True,
    )


__all__ = [
    "project_exact_loan_cover", "project_verified_local_library_cover",
]
