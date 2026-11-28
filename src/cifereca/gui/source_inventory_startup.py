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
from ..plugin_api import SyncResult
from .source_inventory_timer import (
    _MAX_SOURCES, _scheduled_inventory_progress, _broker,
    _configured_identity_ids, _policy, _scheduled_refresh_capable,
)

_MAX_STARTUP_IDENTITIES = 64


def _due_startup_identities(identities: tuple[str, ...], broker: Any) -> tuple[str, ...]:
    """Recheck exact admitted identities against live broker policies.

    Worker-side policy validation must not inspect MainWindow.service:
    switching the displayed account is not cancellation of a queued job.
    """
    now = datetime.now(timezone.utc)
    due: list[str] = []
    for identity in identities:
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


def due_startup_identities(window: Any, broker: Any) -> tuple[str, ...]:
    """Passive GTK admission only; the Core broker revalidates before I/O."""
    if getattr(window, "_closed", False):
        return ()
    return _due_startup_identities(_configured_identity_ids(window), broker)


def dispatch_after_opening(window: Any) -> bool:
    """Queue the bounded inventory refresh; no provider-specific GET worker."""
    if getattr(window, "_closed", False):
        return False
    # Provider download is owned exclusively by canonical source-sync A→B.
    # A legacy startup consent may be read/revoked, but must not queue a
    # second provider-specific acquisition worker.
    if getattr(window, "_source_inventory_startup_dispatched", False):
        return False
    broker = _broker(window)
    if broker is None:
        return False
    identities = due_startup_identities(window, broker)
    if not identities:
        return False
    window._source_inventory_startup_dispatched = True

    def current():
        # A new display-only service is not a revocation of the admitted
        # broker. Broker replacement / window teardown, however, is.
        return not getattr(window, "_closed", False) and _broker(window) is broker

    def work():
        failures = 0
        completed = 0
        deferred = 0
        partials = 0
        changed = 0
        interrupted = False
        for identity_id in identities:
            if not current():
                interrupted = True
                break
            # The user may have revoked a source policy while this worker
            # was queued. Re-probe the read-only opt-in before delegating;
            # the broker then revalidates exact rights/bindings at I/O.
            if identity_id not in _due_startup_identities((identity_id,), broker):
                continue
            if not current():
                interrupted = True
                break
            try:
                receipt = broker.synchronize_scheduled_inventory(
                    identity_id, "startup",
                )
                if type(receipt) is SyncResult and type(receipt.changed) is int and 0 <= receipt.changed <= 256_000_000:
                    changed += receipt.changed
                if type(receipt) is SyncResult and receipt.facts.get("status") in {"busy", "partial", "failed"}:
                    deferred += int(receipt.facts.get("deferred", 0) > 0)
                    partials += int(receipt.facts.get("status") == "partial")
                    failures += int(receipt.facts.get("status") == "failed" or receipt.facts.get("failed", 0) > 0)
                    continue
                progressed, failed = _scheduled_inventory_progress(receipt)
                completed += int(progressed)
                failures += int(failed)
            except Exception:
                # One account's fault does not starve its eligible siblings.
                # Never reflect signed URLs or private identifiers in tasks.
                failures += 1
            if not current():
                interrupted = True
                break
        if partials or (completed and (failures or deferred)):
            return SyncResult(ok=False, changed=changed, facts={"status": "partial",
                                               "completed": completed,
                                               "failed": failures,
                                               "deferred": deferred})
        if deferred and not failures:
            return SyncResult(ok=False, changed=changed, facts={"status": "busy",
                                               "reason": "operation_busy",
                                               "deferred": deferred})
        if completed and failures:
            return SyncResult(ok=False, changed=changed, facts={"status": "partial",
                                               "completed": completed,
                                               "failed": failures})
        if failures and deferred:
            return SyncResult(ok=False, changed=changed, facts={"status": "failed",
                                               "failed": failures,
                                               "deferred": deferred})
        if failures:
            raise RuntimeError(
                "Actualisation initiale des notices des comptes indisponible"
            ) from None
        # The scheduled broker can complete its Core source-sync while this
        # wrapper returns None; do not label that positive Core evidence as
        # a cancelled TaskRunner operation. No attempted source => no success.
        if completed and (interrupted or not current()):
            return SyncResult(ok=False, changed=changed, facts={"status": "partial",
                                               "completed": completed})
        return SyncResult(ok=True, changed=changed) if completed else None

    def after_inventory(_result=None):
        # Scheduled inventory never grants an acquisition or starts a
        # second Numilog-specific downloader after source Sync.
        if getattr(window, "_closed", False) or _broker(window) is not broker:
            return
        if (type(_result) is SyncResult and _result.facts.get("deferred", 0)
                and not getattr(window, "_source_inventory_startup_retried", False)):
            window._source_inventory_startup_retried = True
            from gi.repository import GLib
            def retry():
                if current():
                    window._source_inventory_startup_dispatched = False
                    dispatch_after_opening(window)
                return False
            GLib.timeout_add_seconds(60, retry)

    def inventory_failed(_error):
        # TaskRunner already records this automatic inventory failure.
        # Do not turn a failed inventory worker into an unsolicited modal;
        # report a local hint, without starting a second GET worker.
        # Never forward the exception: it may embed signed provider URLs.
        after_inventory()
        if getattr(window, "_closed", False) or _broker(window) is not broker:
            return
        notify = getattr(window, "toast", None)
        if callable(notify):
            notify("Actualisation des notices des comptes à vérifier dans Synchronisation.")

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
        return False
    return True
