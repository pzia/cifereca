"""Optional descriptive replay ABI, independent of provider sessions.

The read callable is supplied by Core and consumes only captured responses.
A plugin must reuse its pure parser, never create a live session, make network
calls, mutate state or access credentials. None means unqualified coverage.
For loans, rows are (loan_id, resource_id, title, author or empty string).
An empty tuple attests empty only after the parser validates completeness.
"""
from typing import Protocol, Callable
import httpx


class ProviderHealthReplay(Protocol):
    def health_replay_projection(self, capability: str,
                                 read: Callable[[str, str], httpx.Response]
                                 ) -> tuple[tuple[str, str, str, str], ...] | None: ...
