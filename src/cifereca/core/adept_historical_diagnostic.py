"""Private, read-only historical loan diagnostic (#976).

Inventory, canonical publication records and local file integrity are distinct
observations at different times. No new evidence of rights, activation, or
transaction-specific receipt-to-placement provenance is produced.
"""
from __future__ import annotations

from pathlib import Path
import sqlite3

from .adept_historical_evidence import review_historical_adept_evidence
from .adept_historical_inventory_join import review_legacy_adept_inventory
from .adept_historical_publication_review import review_legacy_adept_publication
from .canonical_publication import _stable
from .canonical_recovery import _scope
from .source_convergence import SourceKey


def _coordinate(value, *, empty=False):
    return (type(value) is str and (empty or bool(value)) and len(value) <= 1024
            and all(32 <= ord(ch) != 127 for ch in value))


def _candidate(storage, source):
    """Select only a unique recorded receipt and present local placement."""
    coords = tuple(_scope(source))
    if (len(coords) != 8 or not all(_coordinate(value, empty=(index == 3))
                                    for index, value in enumerate(coords))):
        return "scope_unverified", None
    identity = coords[0]
    reference_id = _stable("source", *coords)
    with storage._lock, storage._connect() as db:
        db.execute("BEGIN")
        reference = db.execute(
            "SELECT identity_id,provider_id,credential_id,account_id,branch_id,"
            "edition_id,right_id,reference_kind FROM source_references "
            "WHERE source_reference_id=?", (reference_id,),
        ).fetchone()
        if reference != coords:
            return "reference_unverified", None
        links = db.execute(
            "SELECT artifact_id,identity_id,"
            "CASE WHEN length(CAST(evidence_ref AS BLOB)) BETWEEN 1 AND 1024 "
            "THEN evidence_ref ELSE NULL END FROM origin_links "
            "WHERE source_reference_id=? AND origin_kind='adept' LIMIT 2",
            (reference_id,),
        ).fetchall()
        if not links:
            return "receipt_not_recorded", None
        if len(links) != 1:
            return "ambiguous_receipt", None
        artifact, owner, receipt = links[0]
        if owner != identity or not _coordinate(artifact) or not _coordinate(receipt):
            return "receipt_unverified", None
        placements = db.execute(
            "SELECT CASE WHEN length(CAST(placement_id AS BLOB)) BETWEEN 1 AND 1024 "
            "THEN placement_id ELSE NULL END FROM placements "
            "WHERE artifact_id=? AND identity_id=? AND state='present' LIMIT 2",
            (artifact, identity),
        ).fetchall()
        if not placements:
            return "no_recorded_present_placement", None
        if len(placements) != 1:
            return "ambiguous_present_placements", None
        placement = placements[0][0]
        if not _coordinate(placement):
            return "placement_unverified", None
        return "unique_recorded_candidate", (artifact, receipt, placement)


def _physical_review(storage, source, library_root, publication):
    def report(selection, evidence=None):
        return {"selection": selection, "evidence": evidence}
    if (publication["source_reference_record"] != "recorded"
            or publication["canonical_origin_records"] not in
            {"provider_and_adept_recorded", "adept_only_recorded"}
            or publication["canonical_placement_state"] not in
            {"present_recorded", "mixed_states_recorded"}):
        return report("canonical_candidate_unavailable")
    try:
        observed = _candidate(storage, source)
        selection, candidate = observed
        if candidate is None:
            return report(selection)
        _, receipt, placement = candidate
        evidence = review_historical_adept_evidence(
            storage, source=source, placement_id=placement,
            adept_receipt_ref=receipt, library_root=library_root,
        )
        if (_candidate(storage, source) != observed
                or review_legacy_adept_publication(storage, source=source) != publication):
            return report("candidate_changed")
        return report(selection, evidence)
    except (sqlite3.Error, ValueError, TypeError, RuntimeError, OSError, RecursionError):
        return report("candidate_unverified")


def review_historical_loan_diagnostic(
    storage, *, source: SourceKey, library_root: Path,
    inventory_profile_id: str, inventory_complete: bool, inventory_loans,
    verify_local_bytes: bool = False,
):
    """Compose existing local diagnostic axes for one independently scoped loan.

    The caller supplies the inventory; no runtime request is made. A physical
    read is opt-in and uses the existing bounded no-follow verifier. A
    unique candidate is not a receipt-to-placement join or a repair plan.
    No global SQL/FS snapshot or current provider state is claimed.
    """
    inventory = review_legacy_adept_inventory(
        storage, source=source, inventory_profile_id=inventory_profile_id,
        inventory_complete=inventory_complete, inventory_loans=inventory_loans,
    )
    publication = review_legacy_adept_publication(storage, source=source)
    physical = {"selection": "not_requested", "evidence": None}
    if verify_local_bytes is True and type(source) is SourceKey and source.kind == "loan":
        physical = _physical_review(storage, source, library_root, publication)
    return {
        "inventory": inventory,
        "publication": publication,
        "local_bytes": physical,
        "common_snapshot_attested": False,
        "inventory_attests_origin_link": False,
        "receipt_attests_exact_placement": False,
        "provider_loan_state": "not_checked",
        "activation_attested": False,
        "may_rebind_or_return": False,
        "may_reacquire": False,
    }
