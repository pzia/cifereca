"""Offline, exact-scope review of canonical Paris and ADEPT OriginLinks.

SourceReferences describe Paris rights and accounts. OriginLinks describe
artifact provenance, not Adobe account ownership or a return permission.
A shared SHA never merges distinct source or Adobe loan histories.

Read-only by construction: one SELECT on the pre-existing v14 schema.
No retries, network, filesystem checks, reconciliation or writes.
"""
from __future__ import annotations

from typing import Any


def review_paris_canonical_adept_links(
    storage: Any, *, loan_id: str, item_id: str,
) -> dict[str, Any]:
    """Observe only exact (Paris loan right_id, edition_id) canonical links.

    Both keys must be exact nonempty strings. An absent canonical row is
    unknown even if the legacy acquisition ledger contains a plausible loan.
    The ADEPT OriginLink's identity_id is the *library/source scope*, not
    evidence of the actual Adobe profile used for a fulfillment.
    """
    def report(*, sources: list[dict[str, Any]], reasons: list[str],
               resolution: str) -> dict[str, Any]:
        return {
            "provider_id": "paris",
            "loan_id": loan_id if type(loan_id) is str and "\x00" not in loan_id else None,
            "item_id": item_id if type(item_id) is str and "\x00" not in item_id else None,
            "source_count": len(sources),
            "multiple_sources": len(sources) > 1,
            "link_resolution": resolution,
            "review_reasons": reasons,
            "sources": sources,
            "adobe_profile_resolution": "unknown",
            "return_authorized": False,
            "note": (
                "SourceReference/OriginLink locaux seulement : aucun profil "
                "Adobe ni état de restitution ne peut être déduit d'un SHA "
                "commun ou d'une identité de bibliothèque. Lecture seule."
            ),
        }

    if (type(loan_id) is not str or type(item_id) is not str
            or not loan_id.strip() or not item_id.strip()
            or "\x00" in loan_id or "\x00" in item_id):
        return report(sources=[], reasons=["invalid_exact_scope"],
                      resolution="unknown")

    with storage._lock, storage._connect() as db:
        rows = db.execute(
            "SELECT s.source_reference_id,s.identity_id,s.credential_id,"
            "s.account_id,s.branch_id,s.state,"
            "o.origin_link_id,o.origin_kind,o.identity_id,o.artifact_id,"
            "o.evidence_ref "
            "FROM source_references AS s "
            "LEFT JOIN origin_links AS o ON "
            "o.source_reference_id=s.source_reference_id "
            "AND o.origin_kind IN ('provider','adept') "
            "WHERE s.provider_id='paris' AND s.reference_kind='loan' "
            "AND s.right_id=? AND s.edition_id=? "
            "ORDER BY s.source_reference_id,o.origin_link_id",
            (loan_id, item_id),
        ).fetchall()

    indexed: dict[str, dict[str, Any]] = {}
    # Never join origins through artifact SHA alone. A SHA may legitimately
    # be shared by Paris borrowers who hold *different* Adobe licences.
    for (source_id, identity_id, credential_id, account_id, branch_id, state,
         origin_id, kind, origin_identity, artifact_id, proof) in rows:
        entry = indexed.setdefault(source_id, {
            "source_reference_id": source_id,
            "library_identity_id": identity_id,
            "credential_id": credential_id, "account_id": account_id,
            "branch_id": branch_id, "source_state": state,
            "_links": [],
        })
        if origin_id is not None:
            entry["_links"].append(
                (origin_id, kind, origin_identity, artifact_id, proof)
            )

    sources = []
    conflict = False
    incomplete = False
    missing_receipt = False
    for source_id, entry in sorted(indexed.items()):
        links = entry.pop("_links")
        artifacts: dict[str, dict[str, list[str]]] = {}
        mismatched = False
        for origin_id, kind, origin_identity, artifact_id, proof in links:
            if origin_identity != entry["library_identity_id"]:
                # ADEPT links have no DB-level same-identity constraint;
                # a cross-library link cannot attest the selected source.
                mismatched = True
                continue
            kind_links = artifacts.setdefault(artifact_id, {
                "provider": [], "adept": [], "without_receipt": [],
            })
            kind_links[kind].append(origin_id)
            if type(proof) is not str or not proof.strip():
                # Schema v14 requires a nonempty receipt for provider links
                # but permits an empty ADEPT evidence_ref. Such a row cannot
                # attest even the *local* existence of an Adobe receipt.
                kind_links["without_receipt"].append(origin_id)
                missing_receipt = True
        artifact_evidence = []
        for artifact_id, kinds in sorted(artifacts.items()):
            provider_count = len(kinds["provider"])
            adept_count = len(kinds["adept"])
            missing_count = len(kinds["without_receipt"])
            paired = (provider_count == 1 and adept_count == 1
                      and missing_count == 0)
            if provider_count > 1 or adept_count > 1:
                conflict = True
            if not paired:
                incomplete = True
            artifact_evidence.append({
                "artifact_id": artifact_id,
                "provider_links": provider_count,
                "adept_links": adept_count,
                "links_without_receipt": missing_count,
                "paired_source_artifact_links": paired,
                # The link is only a persisted receipt, never Adobe proof.
                "adobe_profile_id": None,
                "adobe_loan_state": "unknown",
            })
        if mismatched or len(artifacts) > 1:
            conflict = True
        if not artifacts:
            incomplete = True
        sources.append({
            **entry,
            "cross_identity_link": mismatched,
            "artifacts": artifact_evidence,
        })

    if conflict:
        resolution = "conflicting_records"
    elif incomplete:
        resolution = "unpaired_or_missing_links"
    elif sources:
        resolution = "paired_links_unverified"
    else:
        resolution = "unknown"
    reasons = []
    if not sources:
        reasons.append("canonical_source_reference_missing")
    if conflict:
        reasons.append("canonical_origin_links_conflict")
    if incomplete:
        reasons.append("canonical_origin_links_incomplete")
    if missing_receipt:
        reasons.append("canonical_origin_receipt_missing")
    if len(sources) > 1:
        reasons.append("multiple_paris_source_references")
    return report(sources=sources, reasons=reasons, resolution=resolution)
