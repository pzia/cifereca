"""Non-blocking, fail-closed safety boundary for maintenance tools.

Planning always happens in the task executor, never in GTK callbacks.
A destructive action must reacquire the same process-wide acquisition lock
used by opening sync/Kobo acquisition and revalidate its preview under lock.
"""
from __future__ import annotations

from copy import deepcopy
from threading import Event
from typing import Any, Callable

from ..locking import InterProcessLock, LockBusyError
from ..paths import acquisition_lock_file


BUSY_MESSAGE = (
    "Synchronisation en cours. Nettoyage des doublons indisponible "
    "pour le moment ; réessayez une fois terminée."
)
CHANGED_MESSAGE = (
    "La bibliothèque a changé depuis la prévisualisation. "
    "Relancez Prévisualiser avant toute modification."
)


class MaintenanceBusyError(RuntimeError):
    """A concurrent library writer owns the shared acquisition lock."""


class MaintenancePlanChangedError(RuntimeError):
    """The user-visible preview no longer matches the executable plan."""


class MaintenanceCancelledError(RuntimeError):
    """A user explicitly stopped a cancellable maintenance operation."""


class MaintenanceCancelControl:
    """Thread-safe cancellation bridge; no GTK widgets enter worker code."""

    def __init__(self, *, owner_closed: Callable[[], bool] | None = None) -> None:
        self._event = Event()
        self._owner_closed = owner_closed

    def cancel(self) -> None:
        self._event.set()

    def cancelled(self) -> bool:
        return self._event.is_set() or (
            self._owner_closed is not None and self._owner_closed()
        )

    def check_cancelled(self) -> None:
        if self.cancelled():
            raise MaintenanceCancelledError("Opération de maintenance annulée")

    def report(self, **_kwargs: Any) -> None:
        return None


def preview_maintenance_tool(
    tool: Any, context: Any, *, destructive: bool,
    control: Any = None,
) -> Any:
    """Compute off GTK; never publish a plan cancelled before/after scanning."""
    if control is not None:
        control.check_cancelled()
    if not destructive:
        result = tool.plan(context)
    else:
        try:
            with InterProcessLock(acquisition_lock_file()):
                if control is not None:
                    control.check_cancelled()
                # Preserve the exact nested preview confirmed by the user.
                result = deepcopy(tool.plan(context))
        except LockBusyError as exc:
            raise MaintenanceBusyError(BUSY_MESSAGE) from exc
    if control is not None:
        control.check_cancelled()
    return result


def execute_maintenance_tool(tool: Any, context: Any, preview: Any, control: Any, *, destructive: bool) -> Any:
    """Replan atomically with the mutation lock; never replay a stale delete."""
    if not destructive:
        control.check_cancelled()
        return tool.run(preview, control)
    try:
        with InterProcessLock(acquisition_lock_file()):
            control.check_cancelled()
            updated = tool.plan(context)
            # Match the user-reviewed action, not only an opaque plugin ID.
            if (
                updated.tool_id != preview.tool_id
                or updated.facts != preview.facts
                or updated.summary != preview.summary
                or updated.changes != preview.changes
                or updated.warnings != preview.warnings
            ):
                raise MaintenancePlanChangedError(CHANGED_MESSAGE)
            if updated.facts.get("read_failures"):
                raise MaintenancePlanChangedError(CHANGED_MESSAGE)
            control.check_cancelled()
            return tool.run(updated, control)
    except LockBusyError as exc:
        raise MaintenanceBusyError(BUSY_MESSAGE) from exc
