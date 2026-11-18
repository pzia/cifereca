"""Optional ui.page lifecycle notifications (v1), without importing GTK.

A factory opts in by attaching a PageLifecycleV1 instance to the returned
widget as cifereca_page_lifecycle_v1. It is a presentation contract, never an
admission/consent token. All hooks run on the UI owner thread.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable


@dataclass(frozen=True, slots=True)
class PageViewContext:
    route: str
    identity_id: str | None
    account_scope: tuple[str, str, str] | None
    generation: int
    reason: str
    _current: Callable[[bool], bool] = field(repr=False, compare=False)

    def is_current(self, *, require_visible: bool = True) -> bool:
        """Before a UI callback publishes, check this exact mounted view."""
        return self._current(require_visible)


@dataclass(frozen=True, slots=True)
class PageLifecycleV1:
    """Invalidate immediately; project local/cache data only in on_show.

    Set account_scoped=True only for a view which genuinely consumes the
    selected secondary account. No default account is authority.
    A factory without this opt-in retains its existing behavior.
    """
    on_context_changed: Callable[[PageViewContext], None]
    on_show: Callable[[PageViewContext], None]
    on_dispose: Callable[[], None] | None = None
    account_scoped: bool = False
