"""#7092 public contextual known-C review; no rights, private paths or requests."""
from dataclasses import dataclass
from typing import Callable, Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class LocalOriginDetailMatch:
    provider_id: str
    record_id: str
    docbase: str
    edition_id: str
    title: str
    author: str | None
    comparison: str
    stale: bool
    observed_at: int


@dataclass(frozen=True, slots=True, repr=False)
class LocalOriginDetailPage:
    token: str
    page: int
    matches: tuple[LocalOriginDetailMatch, ...]
    known_matches: int
    has_more: bool
    can_continue: bool
    status: str
    metadata_evidence: str
    coverage_complete: bool = False
    provider_access_performed: bool = False
    association_authorized: bool = False


@runtime_checkable
class LocalOriginDetailReadBroker(Protocol):
    """Worker-only exact local review, one transient view per activation.

    The host supplies a revocable view guard. Tokens are opaque and do not
    authorize provider activity, mutation, or selection of a different copy.
    """
    def preview(self, *, identity_id: str, placement_id: str,
                still_current: Callable[[], bool], confirmed: bool = False,
                page_size: int = 20) -> LocalOriginDetailPage | None: ...
    def next_page(self, *, token: str, confirmed: bool = False) -> LocalOriginDetailPage | None: ...
    def cancel(self, *, token: str) -> None: ...
