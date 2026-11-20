"""#4662: typed lender delivery provenance, never a provider/placement hint.

Legacy Placement fields are descriptive. Canonical publication intentionally
leaves them nullable; the exact provider OriginLink supplies the association.
This read-only check does not attest bytes, confer a right or authorize a copy.
"""
from __future__ import annotations

import json

from .borrow_action_scope import borrow_claim_key


def lender_delivery_binding(service, account, notice, placement):
    """Typed reference takes precedence over legacy placement hints.

    A typed source with an absent/conflicting loan receipt never falls back
    to legacy labels. No SQL writer, filesystem read or provider call.
    """
    if type(placement) is not dict or placement.get("identity_id") != account.identity_id:
        return False
    legacy = (placement.get("provider_id") == account.provider_id
              and placement.get("source_item_id") == notice.record_id)
    storage = getattr(service, "storage", None)
    # Historical minimal read facades lack canonical SQL. They cannot attest
    # a typed source or opt into the nullable-placement compatibility path.
    if not callable(getattr(storage, "_connect", None)) or not hasattr(storage, "_lock"):
        return legacy

    key = borrow_claim_key(account, notice)
    if key is None:
        return False
    with storage._lock:
        db = storage._connect()
        if db.in_transaction:
            return False
        with db:
            db.execute("BEGIN")
            sources = db.execute(
                "SELECT source_reference_id,identity_id,account_id,reference_kind,right_id,"
                "evidence_kind,evidence_ref FROM source_references "
                "WHERE provider_id=? AND credential_id=? AND edition_id=? LIMIT 2",
                (account.provider_id, account.credential_id, notice.record_id),
            ).fetchall()
            if not sources:
                return legacy
            if len(sources) != 1:
                return False
            source, owner, borrower, kind, right, evidence, proof = sources[0]
            if ((owner, borrower, kind, evidence) !=
                    (account.identity_id, account.unique_id, "loan", "provider_response")
                    or not right or not proof):
                return False
            # Verify the actual exact Placement and provider OriginLink, not a
            # detached DTO, title, same SHA in a foreign profile or hint alone.
            artifact = placement.get("artifact_id")
            pid = placement.get("placement_id")
            if type(pid) is not str or not pid or type(artifact) is not str or not artifact:
                return False
            rows = db.execute(
                "SELECT p.placement_id,p.artifact_id FROM placements p "
                "JOIN artifacts a ON a.artifact_id=p.artifact_id "
                "AND a.artifact_id='sha256:'||a.sha256 AND a.size>0 "
                "JOIN origin_links o ON o.artifact_id=p.artifact_id "
                "AND o.identity_id=p.identity_id "
                "WHERE p.identity_id=? AND p.state='present' "
                "AND o.source_reference_id=? AND o.origin_kind='provider' "
                "AND o.evidence_ref<>'' GROUP BY p.placement_id LIMIT 2",
                (account.identity_id, source),
            ).fetchall()
            if rows != [(pid, artifact)]:
                return False
            # Bind the exact durable claim in the SAME read snapshot.
            raw = db.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
            if raw is None or type(raw[0]) is not str or not 0 < len(raw[0]) <= 4096:
                return False
            receipt = json.loads(raw[0])
            return (type(receipt) is dict and type(receipt.get("schema")) is int
                    and receipt["schema"] == 1 and receipt.get("operation") == "loan.borrow"
                    and type(receipt.get("operation_id")) is str and bool(receipt["operation_id"])
                    and receipt.get("status") == "confirmed"
                    and receipt.get("request_accepted") is True
                    and receipt.get("verification") == "positive_active_loan"
                    and receipt.get("verified_loan_id") == right)
