"""Passive projection of exact, unambiguous observed-loan display links.

A manual import with a provider-specific observed reference is NOT a
provider acquisition receipt, possession, license or entitlement.
Only the caller's trusted schema validator can scope the observed reference.
"""
from __future__ import annotations

from collections import Counter


def observed_import_display_links(
    storage, placements, local_keys, *, provenance_field: str,
    placement_provider_id: str, validate_scope,
):
    """Return scope-to-local-row hints, never an acquisition authority.

    The storage query is confined to completed manual imports. The
    observation is a display-only witness of a same-identity indexed path,
    artifact and declared scope, not a proof of loan validity or file bytes.
    """
    if (type(provenance_field) is not str or not provenance_field
            or type(placement_provider_id) is not str or not placement_provider_id
            or not callable(validate_scope)):
        return {}
    # Duplicate same-path placement rows are ambiguous, not a trusted choice.
    paths = [p.get("path") for p in placements if type(p) is dict]
    counts = Counter(path for path in paths if type(path) is str)
    by_path = {
        p["path"]: p for p in placements
        if type(p) is dict and type(p.get("path")) is str
        and counts[p["path"]] == 1
    }
    candidates = {}
    for row in storage.acquisitions(provider_id="manual", status="complete"):
        if type(row) is not dict:
            continue
        meta = row.get("metadata") or {}
        if type(meta) is not dict:
            continue
        scope = validate_scope(meta.get(provenance_field))
        if (not scope or row.get("identity_id") != scope[0]
                or row.get("entitlement_id")
                or row.get("strategy") != "manual_acsm"):
            continue
        path = meta.get("path")
        artifact = row.get("artifact_id")
        if type(path) is not str or type(artifact) is not str:
            continue
        placement = by_path.get(path, {})
        placement_id = placement.get("placement_id")
        if type(placement_id) is not str:
            continue
        local_key = local_keys.get((placement_id, artifact))
        if (local_key and placement.get("identity_id") == scope[0]
                and placement.get("provider_id") == placement_provider_id
                and placement.get("artifact_id") == artifact):
            candidates.setdefault(scope, set()).add(local_key)
    return {
        scope: next(iter(keys)) for scope, keys in candidates.items()
        if len(keys) == 1
    }
