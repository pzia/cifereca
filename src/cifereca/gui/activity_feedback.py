from __future__ import annotations

from dataclasses import dataclass, replace


_STATE_LABELS = {
    "running": "En cours",
    "success": "Terminé",
    "error": "Erreur",
}


@dataclass(frozen=True, slots=True)
class ActivityEntry:
    task_id: int
    label: str
    kind: str
    state: str = "running"
    message: str = ""
    acknowledged: bool = False

    @property
    def display_text(self) -> str:
        state = _STATE_LABELS.get(self.state, self.state)
        return f"{state} · {self.message or self.label}"


class ActivityFeedback:
    """Bounded activity history keyed by task id."""

    def __init__(self, *, limit: int = 20) -> None:
        self.limit = max(1, int(limit))
        self._entries: list[ActivityEntry] = []

    def _upsert(self, entry: ActivityEntry) -> ActivityEntry:
        for index, current in enumerate(self._entries):
            if current.task_id == entry.task_id:
                self._entries[index] = entry
                return entry
        self._entries.append(entry)
        if len(self._entries) > self.limit:
            self._entries = self._entries[-self.limit:]
        return entry

    def get(self, task_id: int) -> ActivityEntry | None:
        return next((entry for entry in self._entries if entry.task_id == int(task_id)), None)

    def started(self, task_id: int, label: str, kind: str) -> ActivityEntry:
        return self._upsert(ActivityEntry(int(task_id), str(label), str(kind)))

    def finished(
        self,
        task_id: int,
        label: str,
        kind: str,
        *,
        success: bool,
        error: str = "",
    ) -> ActivityEntry:
        state = "success" if success else "error"
        message = "" if success else str(error or label)
        return self._upsert(ActivityEntry(
            int(task_id), str(label), str(kind), state, message, False,
        ))

    def result(self, task_id: int, message: str, *, success: bool = True) -> ActivityEntry:
        current = self.get(task_id)
        label = current.label if current is not None else str(message)
        kind = current.kind if current is not None else "task"
        state = "success" if success else "error"
        return self._upsert(ActivityEntry(
            int(task_id), label, kind, state, str(message), False,
        ))

    def latest_unacknowledged_error(self) -> ActivityEntry | None:
        return next(
            (
                entry for entry in reversed(self._entries)
                if entry.state == "error" and not entry.acknowledged
            ),
            None,
        )

    def acknowledge_errors(self) -> None:
        self._entries = [
            replace(entry, acknowledged=True) if entry.state == "error" else entry
            for entry in self._entries
        ]

    def rows(self) -> tuple[ActivityEntry, ...]:
        return tuple(reversed(self._entries))
