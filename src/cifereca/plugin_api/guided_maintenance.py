"""Optional additive guided maintenance UI contract.

A maintenance.tool can implement build_guided(host) instead of the standard
plan dialog. The generic host owns worker admission, the exact selected view
and delivery on the UI thread; the contribution returns a widget and disposer.
No window, Storage, provider session or private execution handle crosses it.
"""
from dataclasses import dataclass
from typing import Any, Callable, Protocol, runtime_checkable


@runtime_checkable
class GuidedMaintenanceHost(Protocol):
    @property
    def identity_id(self) -> str: ...
    def is_current(self) -> bool:
        """Inert thread-safe scope check, including activation and view epoch."""
        ...
    def submit(self, label: str, work: Callable[[], Any],
               done: Callable[[Any], None]) -> bool:
        """Admit worker; deliver once on UI thread or discard after revocation."""
        ...


@dataclass(frozen=True, slots=True)
class GuidedMaintenanceView:
    widget: object
    dispose: Callable[[], None]


@runtime_checkable
class GuidedMaintenanceTool(Protocol):
    def build_guided(self, host: GuidedMaintenanceHost) -> GuidedMaintenanceView: ...
