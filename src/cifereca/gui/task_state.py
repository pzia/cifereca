"""Pure lifecycle bookkeeping for GUI background tasks, independent of GTK."""

from __future__ import annotations


def register_task_start(
    active: dict[int, tuple[int, str, str]],
    finished: set[int],
    task_id: int,
    priority: int,
    label: str,
    kind: str,
) -> bool:
    """Reject duplicate or late start callbacks."""
    task_id = int(task_id)
    if task_id in finished or task_id in active:
        return False
    active[task_id] = (int(priority), str(label), str(kind))
    return True


def register_task_finish(
    active: dict[int, tuple[int, str, str]],
    finished: set[int],
    task_id: int,
) -> tuple[bool, str]:
    """Deduplicate completions and preserve a bounded finished-ID history."""
    task_id = int(task_id)
    if task_id in finished:
        return False, "task"
    current = active.pop(task_id, None)
    kind = current[2] if current is not None else "task"
    finished.add(task_id)
    if len(finished) > 100:
        finished.intersection_update(sorted(finished)[-50:])
    return True, kind
