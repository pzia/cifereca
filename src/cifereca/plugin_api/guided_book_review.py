"""Optional local book review presentation, scoped to one admitted Placement.

CoreUI owns the worker queue, activation and selection lease, and rendering.
Plugins receive no GTK window, storage, raw row, or provider session.
"""
from typing import Any, Callable, Protocol, runtime_checkable


@runtime_checkable
class GuidedBookReviewHost(Protocol):
    @property
    def identity_id(self) -> str: ...
    @property
    def placement_id(self) -> str: ...
    def is_current(self) -> bool:
        """Inert worker-safe activation, route and selection check."""
        ...
    def submit(self, label: str, work: Callable[[], Any],
               done: Callable[[Any], None]) -> bool: ...
    def add_group(self, title: str, rows: tuple[tuple[str, str], ...]) -> None: ...
    def add_action(self, action_id: str, title: str, tooltip: str,
                   invoke: Callable[[], None]) -> None: ...
    def toast(self, message: str) -> None: ...


@runtime_checkable
class GuidedBookReviewTool(Protocol):
    def contribute_book_review(self, host: GuidedBookReviewHost) -> Callable[[], None]: ...
