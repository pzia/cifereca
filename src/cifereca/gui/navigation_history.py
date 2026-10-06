from __future__ import annotations
from dataclasses import dataclass
from typing import Any

@dataclass(frozen=True)
class NavigationState:
    route: str
    identity_id: str | None = None
    query_state: Any = None

class NavigationHistory:
    def __init__(self) -> None:
        self._back: list[NavigationState] = []
        self._current: NavigationState | None = None
        self._forward: list[NavigationState] = []

    @property
    def current(self) -> NavigationState | None:
        return self._current

    def visit(self, state: NavigationState) -> bool:
        if self._current == state:
            return False
        if self._current is not None:
            self._back.append(self._current)
        self._current = state
        self._forward.clear()
        return True

    def can_back(self) -> bool:
        return bool(self._back)

    def can_forward(self) -> bool:
        return bool(self._forward)

    def back(self) -> NavigationState | None:
        if not self._back:
            return None
        if self._current is not None:
            self._forward.append(self._current)
        self._current = self._back.pop()
        return self._current

    def forward(self) -> NavigationState | None:
        if not self._forward:
            return None
        if self._current is not None:
            self._back.append(self._current)
        self._current = self._forward.pop()
        return self._current
