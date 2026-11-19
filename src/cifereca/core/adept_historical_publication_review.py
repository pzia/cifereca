"""#976: local canonical publication records for an exact historical loan.

Only the SQLite snapshot is described. Neither a source state, a recorded
OriginLink nor a Placement labelled present attests current rights, Adobe
activation, physical bytes or permission to repair. No network or FS probe.
"""
from __future__ import annotations

import sqlite3

from .source_convergence import SourceKey

_MAX_ROWS = 256
_MAX_ID = 1024


def _id(value):
    return (type(value) is str and 0 < len(value) <= _MAX_ID
            and all(ord(ch) >= 32 and ord(ch) != 127 for ch in value))


def review_legacy_adept_publication(storage, *, source: SourceKey):
    """Return redacted independent SQL observations, not an admission decision."""
    def result(reference, origin="not_checked", placement="not_checked"):
        return {
            "source_reference_record": reference,
            "canonical_origin_records": origin,
            "canonical_placement_state": placement,
            "physical_bytes_attested": False,
            "provider_loan_state": "not_checked",
            "activation_attested": False,
            "may_rebind_or_return": False,
            "may_reacquire": False,
        }

    if type(source) is not SourceKey or source.kind != "loan":
        return result("scope_unverified")
    scope = source.scope
    try:
        with storage._lock, storage._connect() as db:
            db.execute("BEGIN")
            references = db.execute(
                "SELECT source_reference_id FROM source_references "
                "WHERE identity_id=? AND provider_id=? AND credential_id=? "
                "AND account_id=? AND branch_id=? AND edition_id=? "
                "AND right_id=? AND reference_kind='loan' LIMIT 2",
                (scope.identity_id, scope.provider_id, scope.credential_id,
                 scope.account_id, source.branch, source.edition_id, source.right_id),
            ).fetchall()
            if not references:
                return result("not_recorded")
            if len(references) != 1:
                return result("ambiguous_reference")
            ref = references[0][0]
            if not _id(ref):
                return result("reference_unverified")
            origins = db.execute(
                "SELECT origin_kind, identity_id, artifact_id, "
                "CASE WHEN length(CAST(evidence_ref AS BLOB)) BETWEEN 1 AND ? "
                "THEN evidence_ref ELSE NULL END "
                "FROM origin_links WHERE source_reference_id=? "
                "AND origin_kind IN ('provider','adept') LIMIT ?",
                (_MAX_ID, ref, _MAX_ROWS + 1),
            ).fetchall()
            if len(origins) > _MAX_ROWS:
                return result("recorded", "budget_exceeded")
            by_artifact = {}
            for kind, identity, artifact, proof in origins:
                if identity != scope.identity_id or not _id(artifact) or not _id(proof):
                    return result("recorded", "origin_unverified")
                by_artifact.setdefault(artifact, set()).add(kind)
            if not by_artifact:
                return result("recorded", "not_recorded")
            if len(by_artifact) != 1:
                return result("recorded", "ambiguous_artifacts")
            artifact, kinds = next(iter(by_artifact.items()))
            origin = {
                frozenset(("provider", "adept")): "provider_and_adept_recorded",
                frozenset(("provider",)): "provider_only_recorded",
                frozenset(("adept",)): "adept_only_recorded",
            }[frozenset(kinds)]
            row = db.execute(
                "SELECT sha256 FROM artifacts WHERE artifact_id=?", (artifact,)
            ).fetchone()
            if (row is None or type(row[0]) is not str or len(row[0]) != 64
                    or any(ch not in "0123456789abcdef" for ch in row[0])
                    or artifact != "sha256:" + row[0]):
                return result("recorded", "artifact_unverified")
            states = db.execute(
                "SELECT state FROM placements WHERE artifact_id=? "
                "AND identity_id=? LIMIT ?",
                (artifact, scope.identity_id, _MAX_ROWS + 1),
            ).fetchall()
            if len(states) > _MAX_ROWS:
                return result("recorded", origin, "budget_exceeded")
            if not states:
                return result("recorded", origin, "not_recorded_in_profile")
            values = {state for (state,) in states}
            allowed = {"present", "missing", "unverified", "dedup_cleanup_pending"}
            if not values <= allowed:
                return result("recorded", origin, "state_unverified")
            placement = (next(iter(values)) + "_recorded"
                         if len(values) == 1 else "mixed_states_recorded")
            return result("recorded", origin, placement)
    except (sqlite3.Error, ValueError, TypeError, RuntimeError):
        return result("storage_unverified")
