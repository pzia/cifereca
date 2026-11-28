"""Optional descriptive replay ABI, independent of provider sessions.

The active provider class declares an ordered tuple of flat installed Python
module names (no paths); the final module defines parse_projection. Core
snapshots only those sources and executes them after worker confinement.
Imports are restricted to reviewed pure standard-library modules and the
declared modules. No session, broker, network or filesystem is available.
Rows for loans: (loan_id, resource_id, title, author or empty string).
An empty tuple attests empty only after the parser validates completeness.
"""
from typing import Protocol, Callable, Mapping


class RecordedResponse(Protocol):
    status_code: int
    content: bytes
    headers: Mapping[str, str]
    encoding: str


class ProviderHealthReplay(Protocol):
    health_replay_modules: tuple[str, ...]


class HealthReplayParser(Protocol):
    def __call__(self, capability: str,
                 read: Callable[[str, str], RecordedResponse]
                 ) -> tuple[tuple[str, str, str, str], ...] | None: ...
