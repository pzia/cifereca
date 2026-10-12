"""Lifetime gate for Settings widget callbacks (CoreUI P0 #1127).

The Settings page has asynchronous save / LLM probe callbacks. A controller may
complete a task after the associated page or window has closed or been rebuilt.
These gates are *presentation only*: disposing a page does not cancel a Core
transaction, mutate durable settings or authorize a network request.
"""
from __future__ import annotations


class SettingsPageLifetime:
    """Single-use, monotonic token guard for one Settings page instance."""

    def __init__(self) -> None:
        self._alive = True
        self._version = 0

    @property
    def alive(self) -> bool:
        return self._alive

    def new_token(self) -> int:
        """Invalidate older UI responses, without touching ongoing tasks."""
        self._version += 1
        return self._version

    def accepts(self, token: int) -> bool:
        return self._alive and type(token) is int and token == self._version

    def dispose(self) -> None:
        """Idempotently revoke pending UI responses for this page."""
        if self._alive:
            self._alive = False
            self._version += 1
