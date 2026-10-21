"""Thread-safe, reusable per-row progress relay for maintenance tools (#1492).

Workers only call report(); GTK is updated by an injected main-loop scheduler.
A generation prevents stale jobs from reviving a refreshed or cancelled row.
"""
from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from typing import Callable


# The Core reconciliation broker emits these exact literals. Arbitrary plugin
# progress prose can include account names, source URLs or local paths and
# must never appear in the GTK status line (#2565). This is a presentation
# allowlist, not a claim that a plugin's phase is authoritative.
_RECONCILIATION_PHASES = frozenset({
    "Inventaire local",
    "Lecture des preuves locales",
    "Comparaison des preuves locales",
    "Comparaison des identifiants bibliographiques",
    "Vérification des chemins enregistrés",
    "Audit local terminé",
})


@dataclass(frozen=True)
class MaintenanceLineState:
    phase: str = "idle"
    label: str = "Au repos"
    fraction: float | None = None


class MaintenanceLineProgress:
    """One instance per rendered tool row, not per plugin or global task."""

    def __init__(
        self,
        *,
        idle_add: Callable[[Callable[[], bool]], object],
        render: Callable[[MaintenanceLineState], None],
        alive: Callable[[], bool] = lambda: True,
        activity_label: str = "Purge",
    ) -> None:
        self._idle_add = idle_add
        self._render = render
        self._alive = alive
        # Labels are Core-owned constants; reject plugin-provided free text.
        self._activity_label = (
            activity_label if activity_label in ("Purge", "Réconciliation", "Vérification des couvertures", "Vérification des langues", "Audit des noms", "Renormalisation des noms", "Nettoyage des doublons")
            else "Traitement"
        )
        self._lock = Lock()
        self._generation = 0
        self._disposed = False
        self._scheduled = False
        self._state = MaintenanceLineState()

    @property
    def state(self) -> MaintenanceLineState:
        with self._lock:
            return self._state

    def _publish(
        self, state: MaintenanceLineState, *, ticket: int | None = None,
        phases: tuple[str, ...] = (),
    ) -> None:
        with self._lock:
            if (self._disposed or
                (ticket is not None and ticket != self._generation) or
                (phases and self._state.phase not in phases)):
                return
            self._state = state
            if self._scheduled:
                return
            self._scheduled = True
        try:
            self._idle_add(self._deliver)
        except Exception:
            # Scheduling may be unavailable during GTK teardown. Never leak
            # the failure into worker callbacks or block further publication.
            with self._lock:
                self._scheduled = False

    def _deliver(self) -> bool:
        with self._lock:
            self._scheduled = False
            if self._disposed:
                return False
            current = self._state
        if self._alive():
            self._render(current)
        return False

    def begin(self) -> int:
        with self._lock:
            if self._disposed:
                return self._generation
            self._generation += 1
            ticket = self._generation
        self._publish(MaintenanceLineState("analysis", "Analyse…"), ticket=ticket)
        return ticket

    def _current(self, ticket: int, *, phases: tuple[str, ...] = ()) -> bool:
        with self._lock:
            return (
                not self._disposed
                and ticket == self._generation
                and (not phases or self._state.phase in phases)
            )

    def confirmation(self, ticket: int) -> None:
        self._publish(
            MaintenanceLineState("confirmation", "Confirmation attendue"),
            ticket=ticket, phases=("analysis",),
        )

    def running(self, ticket: int) -> None:
        self._publish(
            MaintenanceLineState("running", f"{self._activity_label} en cours…"),
            ticket=ticket, phases=("confirmation",),
        )

    def report(
        self, ticket: int, *, current: int | None = None,
        total: int | None = None, message: str = "",
    ) -> None:
        valid = (
            type(current) is int and type(total) is int
            and total > 0 and 0 <= current <= total
        )
        if self._activity_label == "Réconciliation":
            # Phase names (not a plugin-provided numeric total) are the only
            # presentation authority for a reconciliation audit. Its recursive
            # inventory has no trustworthy denominator: even current=total
            # cannot establish percentage completion. No arbitrary message
            # can override the phase or smuggle private plugin text.
            if type(message) is not str or message not in _RECONCILIATION_PHASES:
                return
            if (message == "Inventaire local" and type(current) is int
                    and 0 <= current <= 1_000_000):
                label = f"Réconciliation · Inventaire local · {current} fichier(s)"
            else:
                label = f"Réconciliation · {message}…"
            fraction = None
        elif valid:
            # Known-total maintenance tools may report their own progress.
            # 100% is reserved exclusively for a successful terminal callback.
            percent = min(99, (current * 100) // total)
            label = f"{self._activity_label} en cours · {percent} %"
            fraction = percent / 100.0
        else:
            label = f"{self._activity_label} en cours…"
            fraction = None
        self._publish(MaintenanceLineState("running", label, fraction), ticket=ticket, phases=("running",))

    def finish(self, ticket: int, status: str) -> None:
        if status not in ("success", "error", "cancelled"):
            raise ValueError("invalid terminal maintenance status")
        state = {
            "success": MaintenanceLineState("success", "Terminé · 100 %", 1.0),
            "error": MaintenanceLineState("error", "Échec"),
            "cancelled": MaintenanceLineState("cancelled", "Annulé"),
        }[status]
        self._publish(state, ticket=ticket, phases=("analysis", "confirmation", "running"))

    def dispose(self) -> None:
        with self._lock:
            self._disposed = True
            self._generation += 1
            self._scheduled = False

    def valid(self, ticket: int) -> bool:
        return self._current(ticket)
