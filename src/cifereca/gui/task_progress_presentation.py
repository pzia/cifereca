"""Pure presentation of active background-task progress (no GTK dependency)."""

from __future__ import annotations

from collections.abc import Mapping


def live_task_progress_label(
    active_tasks: Mapping[int, tuple[int, str, str]], progress_label: str
) -> str | None:
    """Return a banner for a live task, never for an already finished one.

    Legacy progress callbacks carry a label rather than a task ID. When the
    label no longer identifies an active task, show the highest-priority
    surviving task instead of resurrecting stale progress.
    """
    if not active_tasks:
        return None
    count = len(active_tasks)
    prefix = f"{count} opérations · " if count > 1 else ""
    label = str(progress_label)
    if not any(
        label == active or label.startswith(active + " ")
        for _priority, active, _kind in active_tasks.values()
    ):
        _priority, label, _kind = min(
            active_tasks.values(), key=lambda row: row[0]
        )
    return "En cours · " + prefix + label
