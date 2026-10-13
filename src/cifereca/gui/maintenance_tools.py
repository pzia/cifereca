"""Non-blocking, fail-closed safety boundary for maintenance tools.

Planning always happens in the task executor, never in GTK callbacks.
A destructive action must reacquire the same process-wide acquisition lock
used by opening sync/Kobo acquisition and revalidate its preview under lock.
"""
from __future__ import annotations

from typing import Any

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


def preview_maintenance_tool(tool: Any, context: Any, *, destructive: bool) -> Any:
    """Compute a plan off the GTK thread; refuse competing destructive scans."""
    if not destructive:
        return tool.plan(context)
    try:
        with InterProcessLock(acquisition_lock_file()):
            return tool.plan(context)
    except LockBusyError as exc:
        raise MaintenanceBusyError(BUSY_MESSAGE) from exc


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
