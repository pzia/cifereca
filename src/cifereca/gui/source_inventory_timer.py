"""GTK-owned, opt-in private-store inventory timer (#1346).

A heartbeat only exists when a *per-source* periodic inventory policy is
present. All due decisions are passive broker reads; workers revalidate the
exact identity/account inside SynchronizationBrokerImpl before network I/O.
No acquisition, ADEPT, file operation, legacy backup flag or implicit consent.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from ..core.source_refresh_policy import (
    SourceRefreshSchedule, source_refresh_due,
)
from ..task_queue import TaskPriority

_HEARTBEAT_SECONDS = 60
_MAX_IDENTITIES = 64
_MAX_SOURCES = 256


def _broker(window: Any) -> Any:
    manager = getattr(window, "plugin_manager", None)
    try:
        result = manager.broker("sync") if manager is not None else None
    except Exception:
        return None
    if not all(callable(getattr(result, method, None)) for method in (
        "sources", "inventory_refresh_schedule",
        "synchronize_scheduled_inventory",
    )):
        return None
    return result


def _policy(value: Any) -> SourceRefreshSchedule | None:
    """Treat provider/broker-returned data as untrusted and never echo it."""
    if (
        type(value) is not dict
        or set(value) != {"mode", "interval_minutes"}
    ):
        return None
    try:
        return SourceRefreshSchedule(
            mode=value["mode"], interval_minutes=value["interval_minutes"],
        )
    except (ValueError, TypeError):
        return None


def _configured_identity_ids(window: Any) -> tuple[str, ...]:
    identities = getattr(getattr(window, "service", None), "identities", ())
    if not isinstance(identities, (list, tuple)) or len(identities) > _MAX_IDENTITIES:
        return ()
    values = tuple(getattr(identity, "identity_id", None) for identity in identities)
    if any(type(value) is not str or not value for value in values):
        return ()
    if len(values) != len(set(values)):
        return ()  # ambiguous identity configuration cannot dispatch a batch
    return values


def _scheduled_refresh_capable(source: Any) -> bool:
    """Use only the Sync broker's explicit capability, never a provider ID.

    A malformed/unversioned source projection cannot authorize scheduled I/O.
    The broker still revalidates account, connection, consent and phase at GET.
    """
    provider = getattr(source, "provider_id", None)
    caps = getattr(source, "sync_capabilities", None)
    return (
        type(provider) is str and bool(provider.strip()) and "\0" not in provider
        and type(caps) is tuple and 0 < len(caps) <= 32
        and all(type(cap) is str and 0 < len(cap) <= 80 for cap in caps)
        and "scheduled_refresh" in caps
    )


def _automatic_opening_in_flight(window: Any) -> bool:
    """Defer a periodic refresh while the global automatic opening owns Sync.

    No task cancellation, clock advance or false failed receipt: the next
    heartbeat re-evaluates eligibility. Missing API on older test hosts does
    not add an authority; an unusable installed probe fails closed.
    """
    runner = getattr(window, "runner", None)
    probe = getattr(runner, "is_active_automatic_opening", None)
    if probe is None:
        return False
    try:
        return probe() is not False if callable(probe) else True
    except Exception:
        return True


def periodic_identities_due(
    window: Any, broker: Any, *, now: datetime | None = None,
) -> tuple[str, ...]:
    """Passive bounded eligibility probe; *never* a network/provider call."""
    if getattr(window, "_closed", False):
        return ()
    instant = datetime.now(timezone.utc) if now is None else now
    if not isinstance(instant, datetime) or instant.tzinfo is None:
        return ()
    due: list[str] = []
    for identity_id in _configured_identity_ids(window):
        try:
            rows = broker.sources(identity_id)
            if not isinstance(rows, (tuple, list)) or len(rows) > _MAX_SOURCES:
                continue
            for source in rows:
                try:
                    if (
                        getattr(source, "identity_id", None) != identity_id
                        or not _scheduled_refresh_capable(source)
                        or getattr(source, "sync_capable", None) is not True
                    ):
                        continue
                    sid = getattr(source, "source_id", None)
                    if type(sid) is not str or not sid.startswith("sync:"):
                        continue
                    policy = _policy(broker.inventory_refresh_schedule(sid))
                    if policy is None:
                        continue
                    # The source-level clock is shared with opening/loan sync.
                    # Only discover_rights phase evidence may throttle a private-store
                    # inventory refresh. Legacy test/dummy brokers without phase
                    # states keep their original passive source projection.
                    phase_rows = getattr(source, "phase_states", ())
                    if phase_rows:
                        if not isinstance(phase_rows, (tuple, list)) or len(phase_rows) > 8:
                            continue
                        matches = [
                            phase for phase in phase_rows
                            if getattr(phase, "phase", None) == "discover_rights"
                        ]
                        if len(matches) != 1:
                            continue
                        last_inventory_attempt = getattr(matches[0], "last_attempt", None)
                        inventory_status = getattr(matches[0], "status", None)
                        if (
                            type(last_inventory_attempt) is not str
                            or inventory_status not in {
                                "idle", "running", "success", "partial", "failed",
                            }
                        ):
                            continue
                    else:
                        if getattr(source, "provider_id", None) == "numilog":
                            continue  # Numilog requires its own attested inventory clock.
                        last_inventory_attempt = getattr(source, "last_attempt", "")
                        inventory_status = getattr(source, "status", "idle")
                    if getattr(source, "status", None) == "running":
                        inventory_status = "running"
                    if source_refresh_due(
                        policy, trigger="periodic", now=instant,
                        last_attempt=last_inventory_attempt,
                        phase_status=inventory_status,
                        enabled=getattr(source, "enabled", None),
                        connected=getattr(source, "connected", None),
                        capable=True,
                    ):
                        due.append(identity_id)
                        break
                except Exception:
                    # One malformed or unavailable source must not starve siblings.
                    continue
        except Exception:
            # Missing/broken identity state never authorizes scheduled I/O.
            continue
    return tuple(due)


def stop(window: Any, *, source_remove: Callable[[int], Any]) -> None:
    """Idempotent lifecycle release, including a callback queued at teardown."""
    timer_id = getattr(window, "_source_inventory_timer_id", None)
    window._source_inventory_timer_id = None
    if timer_id is not None:
        try:
            source_remove(timer_id)
        except Exception:
            pass
    unsubscribe = getattr(window, "_source_inventory_unsubscribe", None)
    window._source_inventory_unsubscribe = None
    window._source_inventory_subscription_broker = None
    if callable(unsubscribe):
        try:
            unsubscribe()
        except Exception:
            pass


def configure(
    window: Any, *,
    timeout_add_seconds: Callable[[int, Callable[[], bool]], int],
    source_remove: Callable[[int], Any],
    idle_add: Callable[[Callable[[], bool]], Any],
) -> bool:
    """Install/remove one periodic timer; changes are observed via broker subscribe.

    This method runs on GTK main thread only. A worker-side broker event may
    only post a callback through idle_add, never touch GTK widget state.
    """
    if getattr(window, "_closed", False):
        stop(window, source_remove=source_remove)
        return False

    broker = _broker(window)
    old_broker = getattr(window, "_source_inventory_subscription_broker", None)
    if old_broker is not broker:
        old_unsubscribe = getattr(window, "_source_inventory_unsubscribe", None)
        window._source_inventory_unsubscribe = None
        window._source_inventory_subscription_broker = broker
        if callable(old_unsubscribe):
            try:
                old_unsubscribe()
            except Exception:
                pass
        if broker is not None and callable(getattr(broker, "subscribe", None)):
            def changed():
                if not getattr(window, "_closed", False):
                    def recompute() -> bool:
                        if not getattr(window, "_closed", False):
                            configure(
                                window,
                                timeout_add_seconds=timeout_add_seconds,
                                source_remove=source_remove,
                                idle_add=idle_add,
                            )
                        return False
                    idle_add(recompute)
            try:
                window._source_inventory_unsubscribe = broker.subscribe(changed)
            except Exception:
                window._source_inventory_unsubscribe = None

    # Reconfigure without stacking timers, including after identity/profile
    # changes or when a source is revoked while its worker is running.
    old_id = getattr(window, "_source_inventory_timer_id", None)
    window._source_inventory_timer_id = None
    if old_id is not None:
        try:
            source_remove(old_id)
        except Exception:
            pass
    if broker is None:
        return False

    # Only opt-in *periodic* policies install a heartbeat. Startup/manual
    # preferences do not authorize any timer. No private credentials are read.
    periodic = False
    for identity_id in _configured_identity_ids(window):
        try:
            rows = broker.sources(identity_id)
            if not isinstance(rows, (list, tuple)) or len(rows) > _MAX_SOURCES:
                continue
            for source in rows:
                try:
                    if (
                        getattr(source, "identity_id", None) == identity_id
                        and _scheduled_refresh_capable(source)
                        and getattr(source, "enabled", None) is True
                        and getattr(source, "connected", None) is True
                        and getattr(source, "sync_capable", None) is True
                    ):
                        sid = getattr(source, "source_id", None)
                        if type(sid) is str and sid.startswith("sync:"):
                            policy = _policy(broker.inventory_refresh_schedule(sid))
                            if policy is not None and policy.mode == "periodic":
                                periodic = True
                                break
                except Exception:
                    # One malformed or unavailable source must not starve siblings.
                    continue
        except Exception:
            continue
        if periodic:
            break
    if not periodic:
        return False

    def tick() -> bool:
        if getattr(window, "_closed", False):
            window._source_inventory_timer_id = None
            return False
        current = _broker(window)
        if current is not broker:
            # Replacement/revocation is never a reason to reuse old
            # subscriptions or dispatch with stale broker authority.
            configure(window, timeout_add_seconds=timeout_add_seconds,
                      source_remove=source_remove, idle_add=idle_add)
            return False
        # A periodic tick during opening_sync cannot acquire the same Core
        # write lock. Treat this as deferred work, not a failed source sync.
        if _automatic_opening_in_flight(window):
            return True
        ids = periodic_identities_due(window, broker)
        if not ids:
            return True

        def work():
            failures = 0
            # No unbounded refresh batch and no user-selected-account fallback.
            for identity_id in ids:
                # Opening may have been submitted after the GTK tick but
                # before this queued worker began. Do not enter the broker.
                if _automatic_opening_in_flight(window):
                    return
                if (
                    getattr(window, "_closed", False)
                    or _broker(window) is not broker
                ):
                    # A queued task must not reanimate a replaced plugin
                    # manager/broker after the GTK tick checked its scope.
                    return
                try:
                    receipt = broker.synchronize_scheduled_inventory(identity_id, "periodic")
                    if getattr(receipt, "ok", None) is not True:
                        failures += 1
                except Exception:
                    # One failed account must not starve its eligible siblings.
                    # Keep private exception text out of TaskRunner feedback.
                    failures += 1
            if failures:
                raise RuntimeError("Actualisation des notices des comptes indisponible") from None

        try:
            window.runner.submit(
                "Actualisation périodique des notices des comptes…",
                work, priority=TaskPriority.PROVIDER_SYNC,
                kind="source-sync", origin="automatic",
                coalesce_key="source:inventory:periodic",
            )
        except Exception:
            # A torn-down runner or plugin replacement cannot crash GTK tick.
            pass
        return True

    window._source_inventory_timer_id = timeout_add_seconds(
        _HEARTBEAT_SECONDS, tick,
    )
    return False
