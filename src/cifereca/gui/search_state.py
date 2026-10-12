"""Per-window search state, importable without GTK.

Compatibility descriptors keep the existing MainWindow attribute names while
the state container owns the data. Slots start unset: attributes initialized
only by page construction or the first keyboard/search action remain absent
until that same action, preserving existing hasattr/getattr behavior.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..models import SearchResult
from .search_intent import SearchEnterSequence


@dataclass(slots=True, repr=False, eq=False)
class SearchControllerState:
    selected_id: str | None = field(init=False)
    detail_generation: int = field(init=False)
    last_query: str = field(init=False)
    mode: str = field(init=False)
    local_objects: dict[str, dict[str, Any]] = field(init=False)
    objects: dict[str, SearchResult] = field(init=False)
    remote_results: list[SearchResult] = field(init=False)
    generation: int = field(init=False)
    remote_query_key: tuple[str, str, str] | None = field(init=False)
    online_pending_key: tuple[Any, str | None] | None = field(init=False)
    online_pending_token: object = field(init=False)
    online_pending_generation: int | None = field(init=False)
    enter_sequence: SearchEnterSequence = field(init=False)


class SearchStateField:
    """Data descriptor preserving assignment, lookup and deletion semantics."""

    def __init__(self, state_field: str):
        if state_field not in SearchControllerState.__slots__:
            raise ValueError(f"Unknown search state field: {state_field}")
        self.state_field = state_field

    def __get__(self, instance, owner=None):
        if instance is None:
            return self
        state = getattr(instance, "_search_controller_state", None)
        if state is None:
            raise AttributeError(self.state_field)
        return getattr(state, self.state_field)

    def __set__(self, instance, value):
        state = getattr(instance, "_search_controller_state", None)
        if state is None:
            state = SearchControllerState()
            instance._search_controller_state = state
        setattr(state, self.state_field, value)

    def __delete__(self, instance):
        state = getattr(instance, "_search_controller_state", None)
        if state is None:
            raise AttributeError(self.state_field)
        delattr(state, self.state_field)
