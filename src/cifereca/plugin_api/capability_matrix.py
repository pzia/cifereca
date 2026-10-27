"""#2703: additive, read-only Core/plugin capability evidence contract v1.

A provider code declaration is NOT live provider evidence or permission to
borrow, acquire, copy or decrypt. This module does not inspect storage,
execute plugins, connect providers or cross-join fields with scopes.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
import re

CAPABILITY_MATRIX_CONTRACT = "capability-matrix/1"
CapabilityStatus = Literal["supported", "partial", "unsupported", "unverified"]
ImplementationState = Literal["implemented", "prototype", "planned", "not_applicable"]
CapabilityMode = Literal[
    "server", "local_postfilter_partial", "local_cache", "manual", "unknown",
]
EvidenceKind = Literal["code", "provider_observation", "account_observation", "manual"]

_KEY = re.compile(r"[a-z0-9][a-z0-9_.-]{0,95}\Z")
_MAX_REASON = 512

_STATUSES = {"supported", "partial", "unsupported", "unverified"}
_IMPLEMENTATIONS = {"implemented", "prototype", "planned", "not_applicable"}
_MODES = {"server", "local_postfilter_partial", "local_cache", "manual", "unknown"}
_KINDS = {"code", "provider_observation", "account_observation", "manual"}


@dataclass(frozen=True, slots=True)
class CapabilityEvidence:
    """A single *exact* provider/operation/scope/field observation.

    `enabled`, `connected` and `status` are independent. `supported`
    requires an explicit evidence reference from a qualified source; even
    then this is read-only UI evidence, NOT an executable action admission.
    """
    provider_id: str
    operation: str
    scope: str
    status: CapabilityStatus = "unverified"
    implementation_state: ImplementationState = "not_applicable"
    mode: CapabilityMode = "unknown"
    source_kind: EvidenceKind = "code"
    evidence_ref: str = ""
    reason: str = ""
    field: str = ""
    identity_id: str = ""
    account_id: str = ""
    edition_id: str = ""
    enabled: bool | None = None
    connected: bool | None = None
    schema_version: int = 1

    def __post_init__(self) -> None:
        for key in ("provider_id", "operation", "scope"):
            value = getattr(self, key)
            if type(value) is not str or not _KEY.fullmatch(value):
                raise ValueError("capability_invalid_key")
        for key in ("field", "identity_id", "account_id", "edition_id"):
            value = getattr(self, key)
            if (type(value) is not str or len(value) > 256
                    or "\0" in value or "\n" in value or "\r" in value):
                raise ValueError("capability_invalid_scope")
        if (self.status not in _STATUSES
                or self.implementation_state not in _IMPLEMENTATIONS
                or self.mode not in _MODES or self.source_kind not in _KINDS
                or type(self.schema_version) is not int or self.schema_version != 1):
            raise ValueError("capability_invalid_contract")
        if (type(self.evidence_ref) is not str or len(self.evidence_ref) > 256
                or type(self.reason) is not str or len(self.reason) > _MAX_REASON
                or any(char in self.evidence_ref for char in ("\0", "\n", "\r"))):
            raise ValueError("capability_invalid_evidence")
        if ((self.enabled is not None and type(self.enabled) is not bool)
                or (self.connected is not None and type(self.connected) is not bool)):
            raise ValueError("capability_invalid_state")
        if self.status == "supported":
            if (self.implementation_state != "implemented"
                    or self.source_kind == "code" or not self.evidence_ref):
                raise ValueError("capability_unverified_support")
        if self.status == "partial" and not self.reason:
            raise ValueError("capability_partial_reason_required")

    @property
    def authorizes_mutation(self) -> bool:
        """Always false: use the separate scoped, locked action broker."""
        return False


@dataclass(frozen=True, slots=True)
class CapabilityMatrix:
    """Immutable exact-match lookup; missing field/scope never widens to all."""
    provider_id: str
    observations: tuple[CapabilityEvidence, ...]
    schema_version: int = 1

    def __post_init__(self) -> None:
        if type(self.provider_id) is not str or not _KEY.fullmatch(self.provider_id):
            raise ValueError("capability_invalid_key")
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError("capability_invalid_contract")
        if type(self.observations) is not tuple or len(self.observations) > 512:
            raise ValueError("capability_invalid_contract")
        seen = set()
        for row in self.observations:
            if type(row) is not CapabilityEvidence or row.provider_id != self.provider_id:
                raise ValueError("capability_owner_mismatch")
            key = (row.operation, row.scope, row.field, row.identity_id,
                   row.account_id, row.edition_id)
            if key in seen:
                raise ValueError("capability_duplicate_scope")
            seen.add(key)

    def exact(
        self, operation: str, scope: str, *, field: str = "",
        identity_id: str = "", account_id: str = "", edition_id: str = "",
    ) -> CapabilityEvidence | None:
        """No fallback from public to account, all to author, or A to B."""
        for row in self.observations:
            if (row.operation, row.scope, row.field, row.identity_id,
                    row.account_id, row.edition_id) == (
                    operation, scope, field, identity_id, account_id, edition_id):
                return row
        return None


# The mapping represents only flags already exposed by ProviderCapabilities.
# No promises about browser UI, provider availability or acquisition receipts.
_PROVIDER_FLAGS = (
    ("catalog.search", ("search",)),
    ("personal_inventory.list", ("personal_library",)),
    ("loan.inventory", ("loans",)),
    ("loan.borrow", ("borrow",)),
    ("loan.return", ("return_loan",)),
    ("loan.renew", ("renew_loan",)),
    ("acquisition.download", ("digital_download", "direct_download")),
    ("artifact.backup", ("backup",)),
)


def declared_provider_matrix(
    descriptor: object, *, enabled: bool | None = None,
    connected: bool | None = None,
) -> CapabilityMatrix:
    """Conservative adapter from existing ProviderDescriptor (no live claims).

    Search fields/scopes are NOT a cartesian product. This function produces
    one operation-level record with scope `unknown`; source adapters can
    separately publish evidence for exact supported field/scope pairs.
    """
    provider_id = getattr(descriptor, "id", None)
    flags = getattr(descriptor, "capabilities", None)
    if type(provider_id) is not str or not _KEY.fullmatch(provider_id) or flags is None:
        raise ValueError("capability_invalid_descriptor")
    rows: list[CapabilityEvidence] = []
    for operation, names in _PROVIDER_FLAGS:
        declared = any(getattr(flags, name, False) is True for name in names)
        rows.append(CapabilityEvidence(
            provider_id=provider_id, operation=operation, scope="unknown",
            status="unverified" if declared else "unsupported",
            implementation_state="implemented" if declared else "not_applicable",
            mode="unknown", source_kind="code",
            reason="code_declaration_only" if declared else "not_declared",
            enabled=enabled, connected=connected,
        ))
    return CapabilityMatrix(provider_id, tuple(rows))
