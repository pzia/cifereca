"""Private non-authorizing explanatory receipts for positive purchase observations.

One closed reason per exact A coordinate; no provider payload, queue, rights,
URL, title, historical list or negative catalogue conclusion. Same A proof
and live binding gate reads; a later qualified candidate clears the receipt.
"""
from __future__ import annotations

import json

from ..plugin_api.provider_purchase_enrichment import (
    PurchaseEnrichmentCandidate, PurchaseEnrichmentOmission, _OMISSION_REASONS,
)
from ..plugin_api.provider_rights_observation import ProviderRightsSnapshot
from . import notice_c_job_journal as journal
from .notice_c_purchase_association import _current_a, _digest
from .notice_c_purchase_intent import _allowed_txn

_PREFIX = "notice.c.purchase-omission.v1:"
_MAX = 10000


def proof_for_row(ref, row):
    return _digest(["notice-purchase-current-a.v1", ref, row.evidence_ref, row.observed_at])


def _key(ref):
    if type(ref) is not str or not ref.startswith("source:") or not journal._digest(ref[7:]):
        raise ValueError("invalid_purchase_omission_reference")
    return _PREFIX + ref[7:]


def current_omission_txn(db, ref, proof):
    try:
        if not journal._digest(proof):
            return None
        stored = db.execute("SELECT value FROM metadata WHERE key=?", (_key(ref),)).fetchone()
        if stored is None or type(stored[0]) is not str or len(stored[0]) > 512:
            return None
        value = json.loads(stored[0], object_pairs_hook=journal._unique)
        if (type(value) is not dict or set(value) != {"a", "reason"}
                or value["a"] != proof or type(value["reason"]) is not str
                or value["reason"] not in _OMISSION_REASONS):
            return None
        return value["reason"]
    except (ValueError, TypeError, KeyError, RecursionError):
        return None


def record_purchase_omissions(storage, snapshot, omissions, candidates, *, context_current,
                              permission_current_txn=None):
    """Recheck exact committed positive A after the same-read Core handoff."""
    if (type(snapshot) is not ProviderRightsSnapshot or snapshot.status not in ("success", "partial")
            or snapshot.resume_cursor or type(omissions) is not tuple or type(candidates) is not tuple
            or len(omissions) + len(candidates) > _MAX or not callable(context_current)
            or (permission_current_txn is not None and not callable(permission_current_txn))):
        raise ValueError("invalid_purchase_omission_batch")
    changed = False
    with storage._lock, storage._connect() as db:
        db.execute("BEGIN IMMEDIATE")
        if (context_current() is not True or not _allowed_txn(db, snapshot.scope)
                or (permission_current_txn is not None and permission_current_txn(db) is not True)):
            raise ValueError("purchase_omission_context_changed")
        seen = set()
        rows = {(row.edition_id, row.right_id, row.kind): row for row in snapshot.rights}
        for item in omissions + candidates:
            if type(item) not in (PurchaseEnrichmentCandidate, PurchaseEnrichmentOmission):
                raise ValueError("invalid_purchase_omission_batch")
            source = item.source
            if source in seen or source.scope != snapshot.scope or source.branch != snapshot.branch:
                raise ValueError("purchase_omission_scope_changed")
            seen.add(source)
            row = rows.get((source.edition_id, source.resource_id, "purchase"))
            ref, proof = _current_a(db, source)
            if (row is None or not row.provider_attested or row.state != "active"
                    or row.observed_at != item.observed_at or proof is None
                    or proof_for_row(ref, row) != proof):
                raise ValueError("purchase_omission_source_changed")
            if type(item) is PurchaseEnrichmentCandidate:
                removed = db.execute("DELETE FROM metadata WHERE key=?", (_key(ref),))
                changed |= removed.rowcount == 1
            else:
                if type(item.reason) is not str or item.reason not in _OMISSION_REASONS:
                    raise ValueError("invalid_purchase_omission_reason")
                wire = json.dumps({"a": proof, "reason": item.reason}, separators=(",", ":"))
                updated = db.execute("INSERT INTO metadata(key,value) VALUES(?,?) "
                                     "ON CONFLICT(key) DO UPDATE SET value=excluded.value "
                                     "WHERE metadata.value IS NOT excluded.value", (_key(ref), wire))
                changed |= updated.rowcount == 1
        if (context_current() is not True or not _allowed_txn(db, snapshot.scope)
                or (permission_current_txn is not None and permission_current_txn(db) is not True)):
            raise ValueError("purchase_omission_context_changed")
    # Only report after commit and Storage unlock, never an uncommitted hint.
    return changed
