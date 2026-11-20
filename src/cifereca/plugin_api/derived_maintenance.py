"""#7093 additive public read-only derived maintenance contract.

No journal keys, absolute paths, recovery handles or write authority.
All review tokens are opaque and local to an enabled broker instance.
"""
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class DerivedPendingOverview:
    checked: int
    linked_pending: int
    staged_only: int
    manual_review: int
    complete_pending_page: bool


@dataclass(frozen=True, slots=True)
class DerivedCapacityObservation:
    status: str
    counts: tuple[tuple[str, int], ...]
    record_count: int
    payload_bytes: int
    known_output_bytes: int
    output_bytes_complete: bool
    active_payload_bytes: int
    active_output_bytes: int
    capacity: tuple[tuple[str, int], ...]
    saturation: str
    stock_status: str
    logical_bytes: int | None
    allocated_bytes: int | None
    region_status: str
    region_counts: tuple[tuple[str, int], ...]
    compact_status: str
    compact_counts: tuple[tuple[str, int], ...]
    compact_capacity: tuple[tuple[str, int], ...]
    compact_saturation: str


@dataclass(frozen=True, slots=True, repr=False)
class DerivedReceiptChoice:
    token: str
    parent_relative: str | None
    target_relative: str | None
    output_size: int | None
    media_format: str | None


@dataclass(frozen=True, slots=True, repr=False)
class DerivedReceiptPage:
    token: str
    choices: tuple[DerivedReceiptChoice, ...]
    has_more: bool
    complete: bool


@runtime_checkable
class DerivedMaintenanceReadBroker(Protocol):
    """Explicit worker-only observations and confirmed no-write simulation."""
    def overview(self, *, identity_id: str, confirmed: bool = False) -> DerivedPendingOverview | None: ...
    def capacity(self, *, identity_id: str, confirmed: bool = False) -> DerivedCapacityObservation | None: ...
    def list_receipts(self, *, identity_id: str, confirmed: bool = False) -> DerivedReceiptPage | None: ...
    def next_page(self, *, token: str, confirmed: bool = False) -> DerivedReceiptPage | None: ...
    def verify(self, *, token: str, confirmed: bool = False) -> str | None: ...
    def simulate(self, *, token: str, confirmed: bool = False) -> str: ...
    def cancel(self, *, token: str) -> None: ...
