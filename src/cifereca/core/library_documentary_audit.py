"""#3995: scoped, aggregate-only documentary audit using Core's existing Library.

N1/source evidence and persistent C are NOT an observation of N2 runtime
or N3 bytes. No loader, extraction, refresh or provider dispatch is invoked.
The usual no-scan Library projection still performs its own Placement checks.
"""
from __future__ import annotations

from .covers import resolve_cover
from .local_cover_evidence import VerifiedLocalCoverEvidence
from .notice_c_local_origin_projection import valid_local_public_c_link
from .notice_c_cover_index import read_consolidated_notice_cover
from ..plugin_api import normalize_language_tag

_KEYS = {
    "covers": ("embedded", "origin", "catalog", "ambiguous", "missing", "unknown"),
    "languages": ("canonical", "noncanonical", "invalid", "unknown"),
}
_MAX_ROWS = 10000


def _cover_state(storage, row, owner):
    local = row.get("_exact_local_cover_evidence")
    if (type(local) is VerifiedLocalCoverEvidence
            and local.scope.identity_id == owner
            and local.scope.artifact_id == row.get("artifact_id")
            and local.scope.placement_id == row.get("placement_id")
            and local.scope.source_key == row.get("key")):
        candidates = local.candidates
        if local.embedded is not None:
            candidates += (local.embedded.candidate,)
        decision = resolve_cover(candidates, attested_book_identity=local.scope.source_key)
        if decision.status == "selected":
            return decision.tier
        if decision.status == "ambiguous":
            return "ambiguous"
        return "unknown"
    link = row.get("_public_canonical_cover_link")
    if not valid_local_public_c_link(
        link, identity_id=owner, artifact_id=row.get("artifact_id"),
        placement_id=row.get("placement_id"),
    ):
        return "unknown"
    c = read_consolidated_notice_cover(storage, link.document)
    if c is None or c.epoch != link.epoch:
        return "unknown"
    if tuple(source.source_ref for source in c.sources) != link.source_refs:
        return "unknown"
    # Partial inventory may still contain candidates but cannot certify N1.
    if not c.snapshot_complete:
        return "unknown"
    decision = resolve_cover(c.candidates, attested_book_identity=c.attested_book_identity)
    if decision.status == "selected":
        return decision.tier
    if decision.status == "ambiguous":
        return "ambiguous"
    if c.sources and all(source.state == "missing" for source in c.sources):
        return "missing"
    return "unknown"


def _language_state(row):
    # No inference or attribution from a valid BCP47 tag. An absent or
    # partially unavailable source language remains an explicit unknown.
    meta = row.get("metadata")
    if type(meta) is not dict or len(meta) > 1024:
        return "unknown"
    tags = []
    for container in (row, meta):
        for key in ("language", "languages", "lang", "langs"):
            value = container.get(key)
            if value is None:
                continue
            if type(value) is str:
                value = (value,)
            if type(value) not in (list, tuple) or len(value) > 64:
                return "invalid"
            for tag in value:
                if type(tag) is not str or len(tag) > 128:
                    return "invalid"
                if tag.strip():
                    tags.append(tag.strip())
    if not tags:
        return "unknown"
    canonical = [normalize_language_tag(tag) for tag in tags]
    if any(type(tag) is not str for tag in canonical):
        return "invalid"
    return "canonical" if tags == canonical else "noncanonical"


def library_documentary_audit(service, *, identity_id, kind):
    if type(kind) is not str or kind not in _KEYS:
        raise ValueError("invalid_audit_kind")
    keys = _KEYS[kind]
    def empty():
        return {"schema": 1, "status": "unavailable", "entries": 0,
                **dict.fromkeys(keys, 0)}
    if (type(identity_id) is not str or not 0 < len(identity_id) <= 256
            or identity_id != identity_id.strip() or not identity_id.isprintable()):
        return empty()
    try:
        if service.identity_by_id(identity_id) is None:
            return empty()
        generation = (service._account_context_epoch, service._source_context_epoch)
        rows = service.library_entries(identity_id=identity_id, scan=False)
        if (type(rows) is not list or len(rows) > _MAX_ROWS
                or any(type(row) is not dict or row.get("identity_id") != identity_id
                       for row in rows)):
            return empty()
        counts = dict.fromkeys(keys, 0)
        for row in rows:
            try:
                status = (_cover_state(service.storage, row, identity_id)
                          if kind == "covers" else _language_state(row))
            except (ValueError, TypeError, OSError, RuntimeError, AttributeError):
                status = "unknown"
            counts[status if status in counts else "unknown"] += 1
        if (generation != (service._account_context_epoch, service._source_context_epoch)
                or service.identity_by_id(identity_id) is None):
            return empty()
    except Exception:
        return empty()
    return {"schema": 1, "status": "partial", "entries": len(rows), **counts}
