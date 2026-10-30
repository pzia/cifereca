"""One-shot, opt-in private-store *inventory* refresh after opening sync (#1346).

This deliberately runs only after opening_sync completed and released its
library writer lock. It cannot authorize local materialization, ADEPT or
file downloads; those remain governed by a distinct source consent.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..core.source_refresh_policy import source_refresh_due
from ..task_queue import TaskPriority
from .source_inventory_timer import (
    _MAX_SOURCES, _broker, _configured_identity_ids, _policy,
    _scheduled_refresh_capable,
)

_MAX_STARTUP_IDENTITIES = 64


def due_startup_identities(window: Any, broker: Any) -> tuple[str, ...]:
    """Passive admission only. The Core broker revalidates on worker I/O."""
    if getattr(window, "_closed", False):
        return ()
    now = datetime.now(timezone.utc)
    due: list[str] = []
    for identity in _configured_identity_ids(window):
        try:
            sources = broker.sources(identity)
            if not isinstance(sources, (tuple, list)) or len(sources) > _MAX_SOURCES:
                continue
            for source in sources:
                try:
                    if (
                        getattr(source, "identity_id", None) != identity
                        or not _scheduled_refresh_capable(source)
                        or getattr(source, "enabled", None) is not True
                        or getattr(source, "connected", None) is not True
                        or getattr(source, "sync_capable", None) is not True
                    ):
                        continue
                    source_id = getattr(source, "source_id", None)
                    if type(source_id) is not str or not source_id.startswith("sync:"):
                        continue
                    policy = _policy(broker.inventory_refresh_schedule(source_id))
                    if policy is None or policy.mode != "on_start":
                        continue
                    # A generic source status can describe only opening loans.
                    # Phase-less or malformed inventory evidence cannot authorize
                    # a second automatic provider request.
                    phases = getattr(source, "phase_states", ())
                    if not isinstance(phases, (tuple, list)) or not 1 <= len(phases) <= 8:
                        continue
                    inventory = [
                        phase for phase in phases
                        if getattr(phase, "phase", None) == "discover_rights"
                    ]
                    if len(inventory) != 1:
                        continue
                    phase = inventory[0]
                    status = getattr(phase, "status", None)
                    last_attempt = getattr(phase, "last_attempt", None)
                    if (
                        type(last_attempt) is not str
                        or status not in {
                            "idle", "running", "success", "partial", "failed",
                        }
                    ):
                        continue
                    # The opening worker should have completed, but a manual
                    # second sync may have started between the two callbacks.
                    if getattr(source, "status", "") == "running":
                        continue
                    if source_refresh_due(
                        policy, trigger="startup", now=now,
                        last_attempt=last_attempt, phase_status=status,
                        enabled=True, connected=True, capable=True,
                    ):
                        due.append(identity)
                        break  # the broker performs per-source admission
                except Exception:
                    # A corrupt source cannot suppress another opted-in source
                    # under the same identity or leak private provider errors.
                    continue
        except Exception:
            continue
    return tuple(due[:_MAX_STARTUP_IDENTITIES])


def dispatch_after_opening(window: Any) -> bool:
    """Queue separate bounded inventory and consented backup workers."""
    if getattr(window, "_closed", False):
        return False
    # Both automatic jobs are independent consents, but their Core paths
    # share the *non-blocking* acquisition lock. Never submit both in
    # parallel: a same-startup inventory sync can consume the global lock
    # and permanently spend the one-hour Numilog materialization claim.
    # If there is no due inventory, the consented backup still starts.
    from .numilog_materialization_startup import dispatch_after_opening as materialize
    if getattr(window, "_source_inventory_startup_dispatched", False):
        return False
    broker = _broker(window)
    if broker is None:
        return materialize(window)
    identities = due_startup_identities(window, broker)
    if not identities:
        return materialize(window)
    window._source_inventory_startup_dispatched = True

    def work():
        failures = 0
        for identity_id in identities:
            if getattr(window, "_closed", False) or _broker(window) is not broker:
                return
            # The user may have revoked a source policy while this worker
            # was queued. Re-probe the read-only opt-in before delegating;
            # the broker then revalidates exact rights/bindings at I/O.
            if identity_id not in due_startup_identities(window, broker):
                continue
            try:
                receipt = broker.synchronize_scheduled_inventory(
                    identity_id, "startup",
                )
                if getattr(receipt, "ok", None) is not True:
                    failures += 1
            except Exception:
                # One account's fault does not starve its eligible siblings.
                # Never reflect signed URLs or private identifiers in tasks.
                failures += 1
        if failures:
            raise RuntimeError(
                "Actualisation initiale des notices des comptes indisponible"
            ) from None

    def after_inventory(_result=None):
        # The lock has been released when TaskRunner calls the GTK terminal
        # callback. The source materialization worker rechecks consent and
        # binding on execution; this callback grants no acquisition itself.
        if getattr(window, "_closed", False) or _broker(window) is not broker:
            return
        materialize(window)

    def inventory_failed(_error):
        # A failed *inventory* task must not silently suppress the normal
        # feedback or prevent an independently consented Numilog backup.
        # Never forward the exception: it can embed a signed private URL.
        after_inventory()
        present = getattr(getattr(window, "runner", None), "present_error", None)
        if callable(present):
            present(RuntimeError(
                "Actualisation initiale des notices des comptes indisponible"
            ))

    try:
        window.runner.submit(
            "Actualisation initiale des notices des comptes…", work,
            after_inventory, inventory_failed,
            priority=TaskPriority.PROVIDER_SYNC,
            kind="source-sync", origin="automatic",
            coalesce_key="source:inventory:startup",
        )
    except Exception:
        window._source_inventory_startup_dispatched = False
        return materialize(window)
    return True
