"""Additive #7087 public read-only organizer contract.

No raw Core scope, Artifact/Placement identifier, Storage or execution plan.
All paths are relative to the admitted profile. Tokens are transient, opaque
and never permissions to mutate files or source rights.
"""
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class OrganizerFileAdvice:
    current: str | None
    proposed_name: str | None
    reason: str
    classified_path: str | None
    classified_reason: str | None


@dataclass(frozen=True, slots=True)
class OrganizerGroupAdvice:
    reason: str
    copies: int
    category: str
    category_evidence: str | None
    files: tuple[OrganizerFileAdvice, ...]
    keeper: str | None


@dataclass(frozen=True, slots=True)
class OrganizerDryRunAdvice:
    operation: str
    current: str
    proposed: str
    state: str


@dataclass(frozen=True, slots=True, repr=False)
class OrganizerPageAdvice:
    token: str
    page: int
    placement_count: int
    group_count: int
    groups: tuple[OrganizerGroupAdvice, ...]
    eligible: int
    selected: int
    has_more: bool
    inventory_status: str
    mutation_authorized: bool = False
    dry_run_state: str = ""
    dry_run_entries: tuple[OrganizerDryRunAdvice, ...] = ()
    # Ordinal on this page, never a global placement identifier.
    dry_run_group: int | None = None


@runtime_checkable
class LibraryOrganizerReadBroker(Protocol):
    """Synchronous worker-only calls; exactly one active review per broker."""
    def preview(self, *, identity_id: str, confirmed: bool = False) -> OrganizerPageAdvice | None: ...
    def next_page(self, *, token: str, confirmed: bool = False) -> OrganizerPageAdvice | None: ...
    def select_all_advice(self, *, token: str, confirmed: bool = False) -> OrganizerPageAdvice | None: ...
    def prepare_dry_run(self, *, token: str, confirmed: bool = False,
                        group_index: int | None = None) -> OrganizerPageAdvice | None: ...
    def confirm_dry_run(self, *, token: str, confirmed: bool = False) -> OrganizerPageAdvice | None: ...
    def cancel(self, *, token: str) -> None: ...
