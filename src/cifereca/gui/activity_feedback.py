from __future__ import annotations

from dataclasses import dataclass, replace
import re
from time import monotonic


_STATE_LABELS = {
    "running": "En cours",
    "success": "Terminé",
    "error": "Erreur",
}


def safe_activity_error(message: str) -> str:
    text = str(message or '').strip()
    # Activity is not a raw exception console. Never retain a delivery URL,
    # account address, credential assignment or local path in its history.
    if re.search(r"https?://|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|(?:auth|orderid|resid|rights|password|token|cookie|secret|authorization)[\"\']?\s*[=:]|Bearer\s|(?:/home/|/Users/|[A-Za-z]:\\)", text, re.I):
        return 'L’opération a échoué. Consultez les diagnostics expurgés.'
    return ' '.join(text.split())[:400] or 'L’opération a échoué'


@dataclass(frozen=True, slots=True)
class ActivityEntry:
    task_id: int
    label: str
    kind: str
    state: str = "running"
    message: str = ""
    acknowledged: bool = False
    sequence: int = 0
    updated_at: float = 0.0

    @property
    def operation_label(self) -> str:
        # Operation types are literal UI labels, never arbitrary provider IDs,
        # private titles or URLs supplied as task labels.
        if self.kind == 'obtain' and self.label.casefold() == 'obtention depuis numilog…':
            return 'Télécharger un achat Numilog'
        if self.kind == 'source-sync' and self.label == 'Actualiser mes achats Numilog…':
            return 'Actualiser mes achats Numilog'
        return {
            'search-known': 'Recherche dans le connu',
            'search-online': 'Recherche en ligne',
            'source-sync': 'Synchronisation d’une source',
            'opening-sync': 'Synchronisation au démarrage',
            'obtain': 'Obtention d’un livre',
            'loan-acquisition': 'Obtention ACSM',
        }.get(self.kind, 'Opération')

    @property
    def display_text(self) -> str:
        state = _STATE_LABELS.get(self.state, self.state)
        if self.state == 'error':
            return f"{state} · {self.operation_label} · {self.message or 'L’opération a échoué'}"
        return f"{state} · {self.message or self.label}"


class ActivityFeedback:
    """Bounded activity history keyed by task id."""

    def __init__(self, *, limit: int = 20, clock=monotonic) -> None:
        self.limit = max(1, int(limit))
        self._entries: list[ActivityEntry] = []
        self._clock = clock
        self._sequence = 0

    def _upsert(self, entry: ActivityEntry) -> ActivityEntry:
        self._sequence += 1
        entry = replace(entry, sequence=self._sequence, updated_at=self._clock())
        self._entries = [current for current in self._entries if current.task_id != entry.task_id]
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
        message = "" if success else safe_activity_error(error)
        return self._upsert(ActivityEntry(
            int(task_id), str(label), str(kind), state, message, False,
        ))

    def result(self, task_id: int, message: str, *, success: bool = True) -> ActivityEntry:
        current = self.get(task_id)
        label = current.label if current is not None else str(message)
        kind = current.kind if current is not None else "task"
        state = "success" if success else "error"
        return self._upsert(ActivityEntry(
            int(task_id), label, kind, state, str(message) if success else safe_activity_error(message), False,
        ))

    def latest_completed(self) -> ActivityEntry | None:
        return max((entry for entry in self._entries if entry.state != 'running'),
                   key=lambda entry: entry.sequence, default=None)

    def latest_unacknowledged_error(self) -> ActivityEntry | None:
        return max((entry for entry in self._entries
                    if entry.state == 'error' and not entry.acknowledged),
                   key=lambda entry: entry.sequence, default=None)

    def error_age(self, entry: ActivityEntry) -> str:
        seconds = max(0, int(self._clock() - entry.updated_at))
        if seconds < 60:
            return 'à l’instant'
        if seconds < 3600:
            return f'il y a {seconds // 60} min'
        if seconds < 86400:
            return f'il y a {seconds // 3600} h'
        return f'il y a {seconds // 86400} j'

    def acknowledge_errors(self) -> None:
        self._entries = [
            replace(entry, acknowledged=True) if entry.state == "error" else entry
            for entry in self._entries
        ]

    def rows(self) -> tuple[ActivityEntry, ...]:
        return tuple(reversed(self._entries))
