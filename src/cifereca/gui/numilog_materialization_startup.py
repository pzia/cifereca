"""One-shot startup dispatch for explicitly consented Numilog book downloads.

Separate from inventory policy and task: the inventory trigger never becomes
an acquisition authority. Queue only after opening sync has released its
library writer lock and never run provider/network work on GTK main thread.
"""
from __future__ import annotations

from typing import Any

from ..task_queue import TaskPriority
from ..core.numilog_materialization_startup import eligible_source_ids
from .source_inventory_timer import _broker, _configured_identity_ids


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

    def work():
        failures = 0
        for identity_id in identities:
            if getattr(window, "_closed", False) or _broker(window) is not broker:
                return
            # A revoked policy during queueing cannot dispatch provider I/O.
            if not eligible_source_ids(broker, identity_id):
                continue
            try:
                result = broker.synchronize_scheduled_numilog_materialization(
                    identity_id, "startup",
                )
                if getattr(result, "ok", None) is not True:
                    failures += 1
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
            coalesce_key="source:numilog:materialize:startup",
        )
    except Exception:
        window._numilog_materialization_startup_dispatched = False
        return False
    return True
