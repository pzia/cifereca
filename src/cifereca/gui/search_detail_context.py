"""Pure search-detail callback eligibility policy (#1128)."""

from __future__ import annotations

from typing import TypeAlias

SearchDetailContext: TypeAlias = tuple[int, int, int, str | None]


def detail_callback_is_current(
    *, closed: bool, selected_id: str | None, expected_id: str,
    captured: SearchDetailContext | None, current: SearchDetailContext | None,
) -> bool:
    """Reject callbacks from disposed windows, former selections and old contexts.

    A missing captured context is retained solely for synchronous compatibility.
    """
    if closed or selected_id != expected_id:
        return False
    return captured is None or captured == current
