"""Optional provider.search.stream/1 contract.

Provider-side generators run outside GTK; Core owns ordered validation,
request cancellation and UI delivery.  This does not modify ProviderSession.search.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

from .models import TaskControl

StreamEventKind = Literal["upsert", "remove", "progress", "error", "done", "reset"]


@dataclass(frozen=True, slots=True)
class ProviderSearchEvent:
    """Provider-side event, not a directly displayable UI message.

    Core only publishes event-relevant structured fields. Free-text
    'message' and the opaque 'resume_token' never leave the provider
    stream boundary. Error codes are projected onto a Core-controlled
    public vocabulary; any unknown code becomes 'provider_error'.
    These fields may remain available inside provider-specific transport
    implementations, but must never be logged or exposed through callbacks.
    """
    provider_id: str
    catalog_generation: int
    sequence: int
    kind: StreamEventKind
    item_key: str = ""
    result: object | None = None
    current: int | None = None
    total: int | None = None
    message: str = ""
    error_code: str = ""
    resume_token: str = ""


@runtime_checkable
class ProviderSearchStream(Protocol):
    """Local opt-in for provider adapters advertising catalog.search.stream.

    Provider emits sequences starting at 1 and ending with a 'done' event.
    Retried/replayed sequence numbers may repeat and are ignored by Core;
    a missing sequence causes a full synchronous search fallback.
    Generation always refers to the current source catalog generation.
    The provider must obey the cooperative TaskControl (if supplied).
    """

    def search_stream(
        self, query: str, scope: str = "all", *, field: str = "all",
        content_types: tuple[str, ...] = (), control: TaskControl | None = None,
    ) -> Iterable[ProviderSearchEvent]: ...


__all__ = ["ProviderSearchEvent", "ProviderSearchStream", "StreamEventKind"]
