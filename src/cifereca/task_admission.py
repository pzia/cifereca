"""Opt-in process-local shared admission for independent executors (#2431).

Reservations cover queued AND executing work. This is not a network request
rate limit, running-only semaphore, or cross-process quota. No worker waits.
"""
from __future__ import annotations

from threading import Lock


class AdmissionLease:
    __slots__ = ("_owner", "_scope", "_background", "_released")

    def __init__(self, owner, scope: str, background: bool):
        self._owner = owner
        self._scope = scope
        self._background = background
        self._released = False

    def release(self) -> None:
        with self._owner._lock:
            if self._released:
                return
            self._released = True
            owner = self._owner
            owner._inflight -= 1
            if self._background:
                owner._background -= 1
            count = owner._scope_inflight[self._scope] - 1
            if count:
                owner._scope_inflight[self._scope] = count
            else:
                del owner._scope_inflight[self._scope]

    def __repr__(self) -> str:
        return "AdmissionLease(private)"


class SharedTaskAdmission:
    """Atomic opt-in admission; scopes are private and never logged.

    Priorities >=30 count as background; user priority 0-20 may use the
    reserved lane. No work is queued or blocked by the reservation itself.
    """

    def __init__(self, *, max_inflight: int, reserve_user_slots: int = 1,
                 max_per_scope: int | None = None):
        if type(max_inflight) is not int or max_inflight < 1:
            raise ValueError("max_inflight must be a positive integer")
        if (type(reserve_user_slots) is not int or
                not 0 <= reserve_user_slots < max_inflight):
            raise ValueError("invalid reserve_user_slots")
        if max_per_scope is not None and (
                type(max_per_scope) is not int or max_per_scope < 1):
            raise ValueError("max_per_scope must be a positive integer")
        self.max_inflight = max_inflight
        self.reserve_user_slots = reserve_user_slots
        self.max_per_scope = max_per_scope
        self._lock = Lock()
        self._inflight = 0
        self._background = 0
        self._admitted = 0
        self._rejected = 0
        # Pure aggregate counters: no labels, account IDs, tokens or scopes
        # are retained in diagnostics. Exactly one rejection reason is counted
        # per refusal, in deterministic global > reserved > scope order.
        self._rejected_global = 0
        self._rejected_reserved = 0
        self._rejected_scope = 0
        self._rejected_user = 0
        self._rejected_background = 0
        self._scope_inflight: dict[str, int] = {}

    def acquire(self, *, priority: int, scope: str | None = None) -> AdmissionLease | None:
        if type(priority) is not int:
            raise ValueError("invalid task priority")
        if scope is not None and (
            type(scope) is not str or not 1 <= len(scope) <= 128
        ):
            raise ValueError("invalid task scope")
        private_scope = scope if scope is not None else ""
        background = priority >= 30
        with self._lock:
            # Preserve the pre-existing boolean admission policy verbatim;
            # attribute a refused request to ONE cause for reproducible,
            # privacy-preserving contention diagnostics (#2431/#1741).
            global_full = self._inflight >= self.max_inflight
            reserved_full = (
                background and self._background >=
                self.max_inflight - self.reserve_user_slots
            )
            scope_full = (
                self.max_per_scope is not None
                and self._scope_inflight.get(private_scope, 0) >= self.max_per_scope
            )
            if global_full or reserved_full or scope_full:
                self._rejected += 1
                if global_full:
                    self._rejected_global += 1
                elif reserved_full:
                    self._rejected_reserved += 1
                else:
                    self._rejected_scope += 1
                if background:
                    self._rejected_background += 1
                else:
                    self._rejected_user += 1
                return None
            self._inflight += 1
            self._background += int(background)
            self._admitted += 1
            self._scope_inflight[private_scope] = (
                self._scope_inflight.get(private_scope, 0) + 1
            )
            return AdmissionLease(self, private_scope, background)

    def snapshot(self) -> dict[str, int | None]:
        """Only aggregates, never resource scopes or private tokens."""
        with self._lock:
            return {
                "inflight": self._inflight,
                "background": self._background,
                "admitted": self._admitted,
                "rejected": self._rejected,
                "rejected_global": self._rejected_global,
                "rejected_reserved": self._rejected_reserved,
                "rejected_scope": self._rejected_scope,
                "rejected_user": self._rejected_user,
                "rejected_background": self._rejected_background,
                "max_inflight": self.max_inflight,
                "max_background": self.max_inflight - self.reserve_user_slots,
                "max_per_scope": self.max_per_scope,
                "active_scopes": len(self._scope_inflight),
            }

    def __repr__(self) -> str:
        return "SharedTaskAdmission(private_scopes)"
