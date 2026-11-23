"""#2703: seven-family, exact-scope, read-only capability coverage.

This groups *existing* CapabilityEvidence, never creates a proof, promotes a
code declaration to provider attestation or authorizes an action. A missing
operation/branch/identity stays missing rather than falling back to "all".
No provider, broker, storage, filesystem or network access.
"""
from __future__ import annotations

from dataclasses import dataclass

from .capability_matrix import CapabilityEvidence, CapabilityMatrix


CAPABILITY_FAMILIES = (
    "catalogue", "metadata", "accounts_rights", "loans",
    "acquisition", "drm", "crosscutting",
)


def _family(operation: str) -> str | None:
    """One operation belongs to one family; unknown names remain unclassified."""
    if operation.startswith("catalog."):
        return "catalogue"
    if operation.startswith("metadata."):
        return "metadata"
    if (operation.startswith(("personal_inventory.", "account.", "rights."))
            or operation == "sync.entitlements"):
        return "accounts_rights"
    if (operation.startswith(("loan.", "booking."))
            or operation == "sync.remote_inventory"):
        return "loans"
    if (operation.startswith("acquisition.")
            or operation == "sync.local_materialization"):
        return "acquisition"
    if operation.startswith(("drm.", "transform.")):
        return "drm"
    if (operation.startswith(("artifact.", "cache.", "diagnostics."))
            or operation == "sync.scheduled_refresh"):
        return "crosscutting"
    return None


@dataclass(frozen=True, slots=True)
class CapabilityFamilyCoverage:
    family: str
    evidence: tuple[CapabilityEvidence, ...]
    # If the family has no exact-scope evidence, this is a gap, not a
    # supplier's unsupported verdict. No summary "supported" is fabricated.
    missing: bool
    supported_count: int
    partial_count: int
    unverified_count: int
    unsupported_count: int

    @property
    def authorizes_mutation(self) -> bool:
        return False


def exact_capability_families(
    matrix: CapabilityMatrix, *, scope: str, field: str = "",
    identity_id: str = "", account_id: str = "",
    credential_id: str = "", edition_id: str = "",
) -> tuple[CapabilityFamilyCoverage, ...]:
    """Seven diagnostic buckets, never a provider-wide availability summary.

    Every coordinate must match exactly, including empty account and
    credential IDs. Code-level `scope="unknown"` is not a wildcard.
    `supported_count` counts only already attested operation rows and does
    not mean a family, loan, DRM or source is operational or authorized.
    """
    if type(matrix) is not CapabilityMatrix:
        raise ValueError("capability_invalid_matrix")
    if any(type(value) is not str or len(value) > 256 or
           "\x00" in value or "\n" in value or "\r" in value
           for value in (
               scope, field, identity_id, account_id, credential_id, edition_id,
           )):
        raise ValueError("capability_invalid_scope")
    # Recheck nested frozen DTOs before consuming a cached matrix. A forged
    # row is rejected rather than grouped as newly attested evidence.
    matrix.__post_init__()
    buckets: dict[str, list[CapabilityEvidence]] = {
        family: [] for family in CAPABILITY_FAMILIES
    }
    exact_key = (
        scope, field, identity_id, account_id, credential_id, edition_id,
    )
    for row in matrix.observations:
        family = _family(row.operation)
        if family is None:
            continue
        if (row.scope, row.field, row.identity_id, row.account_id,
                row.credential_id, row.edition_id) == exact_key:
            buckets[family].append(row)
    result = []
    for family, records in buckets.items():
        ordered = tuple(sorted(records, key=lambda row: row.operation))
        result.append(CapabilityFamilyCoverage(
            family=family,
            evidence=ordered,
            missing=not bool(ordered),
            supported_count=sum(row.status == "supported" for row in ordered),
            partial_count=sum(row.status == "partial" for row in ordered),
            unverified_count=sum(row.status == "unverified" for row in ordered),
            unsupported_count=sum(row.status == "unsupported" for row in ordered),
        ))
    return tuple(result)


__all__ = [
    "CAPABILITY_FAMILIES", "CapabilityFamilyCoverage",
    "exact_capability_families",
]
