"""One-shot startup dispatch for explicitly consented Numilog book downloads.

Separate from inventory policy and task: the inventory trigger never becomes
an acquisition authority. Queue only after opening sync has released its
library writer lock and never run provider/network work on GTK main thread.
"""
from __future__ import annotations

from typing import Any

from ..plugin_api import Cancelled
from ..task_queue import TaskPriority
from ..core.numilog_materialization_startup import eligible_source_ids
from .source_inventory_timer import _broker, _configured_identity_ids


class _StartupControl:
    """Cooperative stop between books when the originating host is gone."""
    def __init__(self, window: Any, broker: Any, report: Any = None):
        self.window, self.broker, self.progress = window, broker, report

    def cancelled(self) -> bool:
        return bool(getattr(self.window, '_closed', False)
                    or _broker(self.window) is not self.broker)

    def check_cancelled(self) -> None:
        if self.cancelled():
            raise Cancelled('Sauvegarde Numilog annulée')

    def report(self, *, current: int = 0, **_fields: Any) -> None:
        if self.cancelled() or not callable(self.progress):
            return
        count = current if type(current) is int and 0 <= current <= 512 else 0
        self.progress(f'Sauvegarde Numilog : {count} livre(s)')


def dispatch_after_opening(window: Any) -> bool:
    if (
        getattr(window, "_closed", False)
        or getattr(window, "_numilog_materialization_startup_dispatched", False)
    ):
        return False
    broker = _broker(window)
    if broker is None or not callable(
        getattr(broker, "synchronize_scheduled_numilog_materialization", None)
    ):
        return False
    identities = tuple(
        identity for identity in _configured_identity_ids(window)
        if eligible_source_ids(broker, identity)
    )
    if not identities:
        return False
    window._numilog_materialization_startup_dispatched = True

    def work(report=None):
        control = _StartupControl(window, broker, report)
        failures = 0
        for identity_id in identities:
            control.check_cancelled()
            # A revoked policy during queueing cannot dispatch provider I/O.
            if not eligible_source_ids(broker, identity_id):
                continue
            try:
                result = broker.synchronize_scheduled_numilog_materialization(
                    identity_id, "startup", control=control,
                )
                if getattr(result, "ok", None) is not True:
                    failures += 1
            except Cancelled:
                raise
            except Exception:
                failures += 1
        if failures:
            raise RuntimeError(
                "Sauvegarde initiale Numilog incomplète ; consulter Synchronisation"
            ) from None

    try:
        window.runner.submit(
            "Sauvegarde des achats Numilog consentie…", work,
            priority=TaskPriority.PROVIDER_SYNC,
            kind="source-materialize", origin="automatic",
            coalesce_key="source:numilog:materialize:startup", with_progress=True,
        )
    except Exception:
        window._numilog_materialization_startup_dispatched = False
        return False
    return True
