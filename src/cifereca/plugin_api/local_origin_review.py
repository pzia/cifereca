"""#7092 additive public readonly batch preview; no Core objects or authority."""
from dataclasses import dataclass
from typing import Callable, Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class KnownOriginMatch:
    title: str
    author: str | None
    provider_id: str
    comparison: str
    stale: bool
    # Additive, already retained public-C source coordinates. These are
    # documentary facts only, not an OriginLink or a provider acquisition.
    record_id: str = ""
    docbase: str = ""
    edition_id: str = ""
    medium: str = ""
    media_format: str | None = None
    observed_at: int | None = None


@dataclass(frozen=True, slots=True)
class LocalOriginFileAdvice:
    file: str
    status: str
    reason: str
    known_matches: int | None
    more_matches: bool
    suggestions: tuple[KnownOriginMatch, ...]
    c_window_status: str | None = None
    unindexed_sources: int | None = None


@dataclass(frozen=True, slots=True, repr=False)
class LocalOriginBatchPage:
    token: str
    page: int
    entries: tuple[LocalOriginFileAdvice, ...]
    has_more: bool
    can_continue: bool
    coverage_complete: bool = False
    provider_access_performed: bool = False
    association_authorized: bool = False


@runtime_checkable
class LocalOriginBatchReadBroker(Protocol):
    """Worker-only cache comparison; tokens cannot authorize origin or rights."""
    def preview(self, *, identity_id: str, confirmed: bool = False,
                cancelled: Callable[[], bool] | None = None,
                include_declared: bool = False) -> LocalOriginBatchPage | None: ...
    def next_page(self, *, token: str, confirmed: bool = False,
                  cancelled: Callable[[], bool] | None = None) -> LocalOriginBatchPage | None: ...
    def cancel(self, *, token: str) -> None: ...
