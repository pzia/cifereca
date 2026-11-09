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

# Read-only library fallback must not scan an unbounded or lazy provider view.
_MAX_LOAN_COVER_ROWS = 10000


def materialized_loan_cover_rows(
    library_rows: Iterable[Mapping[str, object]],
) -> tuple[Mapping[str, object], ...]:
    """Bound a UI loan-cover snapshot before retaining it in GTK memory.

    A failing or oversized lazy library projection is not evidence. Discard
    partial rows, preserving the loan inventory independently of its cover.
    """
    rows: list[Mapping[str, object]] = []
    try:
        for index, row in enumerate(library_rows):
            if index >= _MAX_LOAN_COVER_ROWS:
                return ()
            rows.append(row)
    except Exception:
        return ()
    return tuple(rows)


def attested_local_loan_cover_proof(
    loan: Loan | None, entitlement: Mapping[str, object],
    library_row: Mapping[str, object], *,
    identity_id: str, provider_id: str, item_id: str,
) -> dict[str, str] | None:
    """Project one exact loan-to-local-Placement proof, never a guessed join.

    Core's canonical library projection calls this *only after* resolving
    the entitlement's source to an actual local row with a checked Placement.
    The evidence is source-specific, not a property of its shared content SHA.
    """
    if (type(loan) is not Loan or type(entitlement) is not dict
            or type(library_row) is not dict
            or library_row.get("local") is not True
            or library_row.get("identity_id") != identity_id
            or type(loan.identity_id) is not str
            or loan.identity_id != identity_id
            or type(loan.id) is not str or not loan.id
            or type(entitlement.get("loan_id")) is not str
            or entitlement["loan_id"] != loan.id
            or type(loan.record_id) is not str or not loan.record_id
            or loan.record_id != item_id
            or loan.source_provider != provider_id
            or type(entitlement.get("credential_id")) is not str
            or loan.source_credential != entitlement["credential_id"]
            or type(entitlement.get("account_id")) is not str
            or loan.source_account_id != entitlement["account_id"]
            or type(library_row.get("placement_id")) is not str
            or not library_row["placement_id"]
            or type(library_row.get("artifact_id")) is not str
            or not library_row["artifact_id"]):
        return None
    metadata = library_row.get("metadata")
    resolution = metadata.get("cover_resolution") if type(metadata) is dict else None
    if (type(resolution) is not dict
            or resolution.get("status") != "selected"
            or resolution.get("tier") not in ("embedded", "origin", "catalog")
            or type(resolution.get("url")) is not str
            or resolution["url"] != library_row.get("thumbnail_url")
            or candidate(resolution["url"], tier=resolution["tier"],
                         source="verified-local-placement") is None):
        return None
    return {
        "loan_source_key": loan_source_key(loan),
        "identity_id": loan.identity_id,
        "source_provider": loan.source_provider,
        "source_credential": loan.source_credential,
        "source_account_id": loan.source_account_id,
        "record_id": loan.record_id,
        "loan_id": loan.id,
        "artifact_id": library_row["artifact_id"],
        "placement_id": library_row["placement_id"],
        "url": resolution["url"],
        "tier": resolution["tier"],
    }



def attested_remote_loan_cover_proof(
    loan: Loan | None, entitlement: Mapping[str, object],
    library_row: Mapping[str, object], *,
    identity_id: str, provider_id: str, item_id: str,
) -> dict[str, str] | None:
    """Read-only exact remote loan/entitlement proof for a catalogue cover.

    A library row with no local Placement can still have an attested public
    cover. Do not join on title/ISBN or the edition alone: require the same
    borrower, credential, account, provider, edition and loan identifier.
    """
    if (type(loan) is not Loan or type(entitlement) is not dict
            or type(library_row) is not dict
            or library_row.get("local") is not False
            or library_row.get("identity_id") != identity_id
            or library_row.get("provider_id") != provider_id
            or library_row.get("item_id") != item_id
            or library_row.get("entitlement_type") != "loan"
            or library_row.get("loan_id") != loan.id
            or entitlement.get("entitlement_type") != "loan"
            or entitlement.get("identity_id") != identity_id
            or entitlement.get("provider_id") != provider_id
            or entitlement.get("item_id") != item_id
            or entitlement.get("loan_id") != loan.id
            or type(entitlement.get("entitlement_id")) is not str
            or not entitlement["entitlement_id"]
            or library_row.get("entitlement_id") != entitlement["entitlement_id"]
            or type(loan.identity_id) is not str or loan.identity_id != identity_id
            or type(loan.id) is not str or not loan.id
            or type(loan.record_id) is not str or loan.record_id != item_id
            or loan.source_provider != provider_id
            or type(loan.source_credential) is not str
            or not loan.source_credential
            or loan.source_credential != entitlement.get("credential_id")
            or type(loan.source_account_id) is not str
            or loan.source_account_id != entitlement.get("account_id")):
        return None
    metadata = library_row.get("metadata")
    resolution = metadata.get("cover_resolution") if type(metadata) is dict else None
    if (type(resolution) is not dict
            or resolution.get("status") != "selected"
            or resolution.get("tier") not in ("origin", "catalog")
            or type(resolution.get("url")) is not str
            or resolution["url"] != library_row.get("thumbnail_url")
            or candidate(resolution["url"], tier=resolution["tier"],
                         source="verified-remote-loan") is None):
        return None
    return {
        "kind": "remote-entitlement",
        "loan_source_key": loan_source_key(loan),
        "identity_id": identity_id,
        "source_provider": provider_id,
        "source_credential": loan.source_credential,
        "source_account_id": loan.source_account_id,
        "record_id": item_id,
        "loan_id": loan.id,
        "entitlement_id": entitlement["entitlement_id"],
        "url": resolution["url"],
        "tier": resolution["tier"],
    }


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
    try:
        for index, row in enumerate(library_rows):
            if index >= _MAX_LOAN_COVER_ROWS:
                return missing
            if type(row) is not dict or row.get("identity_id") != loan.identity_id:
                continue
            local = row.get("local") is True
            if local and (type(row.get("placement_id")) is not str
                          or not row["placement_id"]
                          or type(row.get("artifact_id")) is not str
                          or not row["artifact_id"]):
                continue
            if not local and (row.get("local") is not False
                              or row.get("entitlement_type") != "loan"
                              or row.get("loan_id") != loan.id
                              or row.get("provider_id") != loan.source_provider
                              or row.get("item_id") != loan.record_id
                              or type(row.get("entitlement_id")) is not str
                              or not row["entitlement_id"]):
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
                        or (local and (
                            proof.get("artifact_id") != row["artifact_id"]
                            or proof.get("placement_id") != row["placement_id"]))
                        or (not local and (
                            proof.get("kind") != "remote-entitlement"
                            or proof.get("entitlement_id") != row["entitlement_id"]
                            or proof.get("url") != row.get("thumbnail_url")
                            or type(row.get("metadata")) is not dict
                            or row["metadata"].get("cover_resolution", {}).get("status") != "selected"
                            or row["metadata"]["cover_resolution"].get("url") != proof.get("url")
                            or row["metadata"]["cover_resolution"].get("tier") != proof.get("tier")
                        ))):
                    continue
                tier = proof.get("tier")
                if tier not in ("embedded", "origin", "catalog"):
                    continue
                # Equal-strength conflicting evidence must remain ambiguous.
                cover = candidate(
                    proof.get("url"), tier=tier, source="verified-local-placement",
                    evidence_id=str(row["placement_id"] if local else row["entitlement_id"]), evidence_strength=100,
                )
                if cover is not None:
                    candidates.append(cover)
    except Exception:
        # A lazy projection may fail mid-stream. Never return a partial
        # candidate set or allow provider/private exception text to escape.
        return missing
    return resolve_cover(candidates)


__all__ = ["attested_local_loan_cover_proof", "attested_remote_loan_cover_proof", "loan_local_cover", "materialized_loan_cover_rows"]
