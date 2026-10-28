"""Private Core handoff of existing stored-path observations (#2847/#3638).

No filesystem/SQL reads, scan, right inference or repair token. The broker
owns freshness and scope validation. This envelope never crosses its public
plugin return boundary; Diagnostics owns any later journal projection.
"""
from __future__ import annotations

import secrets

from .diagnostic_acquisition_failure import protect_text

_STATES = {
    "stored_path_present": ("present", "regular_file_observed"),
    "stored_path_missing": ("missing", "enoent_observed"),
    "stored_path_unavailable": ("unavailable", "probe_unavailable"),
    "stored_path_out_of_scope": ("unverified", "out_of_scope"),
}
MAX_OBSERVATIONS = 256
_REASONS = frozenset({
    "regular_file_observed", "enoent_observed", "probe_unavailable", "out_of_scope",
    "invalid_path", "not_regular_file", "permission_denied", "symlink_or_not_directory",
    "io_unavailable", "root_changed", "path_changed", "root_missing",
    "root_permission_denied", "root_unavailable",
})


def _text(value):
    return value if (type(value) is str and 0 < len(value) <= 4096
                     and not any(ch in value for ch in "\0\r\n")) else None


def placement_observations(audit, identity_id: str) -> dict:
    """Project attested SQLite values plus states from the SAME Core audit.

    Missing IDs remain absent. A row not examined by the stored-path phase
    remains unverified, even if the filesystem inventory did not see it.
    The bound is explicit and never changes a truncated row into missing.
    """
    from .library_audit import LibraryAudit

    if type(audit) is not LibraryAudit or _text(identity_id) is None:
        raise ValueError("invalid_placement_observation_source")
    rows = audit.snapshot.tables.get("placements", [])
    observations = []
    total = 0
    for index, row in enumerate(rows):
        if type(row) is not dict or row.get("identity_id") != identity_id:
            continue
        total += 1
        if len(observations) >= MAX_OBSERVATIONS:
            continue
        state, reason = _STATES.get(
            audit.stored_paths.states.get(index), ("unverified", "not_examined"),
        )
        observed_reason = audit.stored_paths.observation_reasons.get(index)
        if observed_reason in _REASONS:
            reason = observed_reason
        observation = {
            "state": state, "reason": reason,
            "observation_source": "core_stored_paths",
            "restoration": "not_evaluated",
        }
        for key in ("placement_id", "artifact_id", "state"):
            value = _text(row.get(key))
            if value is not None:
                observation["sqlite_state" if key == "state" else key] = value
        for key in ("provider_id", "source_item_id"):
            value = _text(row.get(key))
            if value is not None:
                observation["recorded_" + key] = value
        path = _text(row.get("path"))
        if path is not None:
            observation["path"] = protect_text(path)
            if observation["path"] != path:
                observation["path_credentials_removed"] = True
        observations.append(observation)
    return {
        "schema": "cifereca.placement.observation/1",
        # An actual batch occurrence, not a business ID or a hash-derived right.
        "observation_ref": secrets.token_hex(16),
        "identity_id": identity_id,
        "scope_validated": True, "read_only": True,
        "repair_authorized": False, "acquisition_authorized": False,
        "probe_coverage": "partial" if audit.stored_paths.reasons else "complete",
        "inventory_coverage": "partial" if audit.inventory.reasons else "complete",
        "reasons": sorted(audit.stored_paths.reasons),
        "total_rows": total, "emitted_rows": len(observations),
        "truncated": total > len(observations),
        "observations": observations,
    }
