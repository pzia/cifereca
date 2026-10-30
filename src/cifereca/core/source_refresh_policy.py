"""Opt-in, per-source inventory/rights refresh policy (P0 #1346).

Storage and due decisions only. This module deliberately does NOT install a
timer, call a provider, authorize local acquisitions or migrate the legacy
global Kobo backup switch into a source permission.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

_SCHEMA = "cifereca.source-refresh-policy/1"
_SOURCE_ID = re.compile(r"sync:[0-9a-f]{24}\Z")
_MODES = frozenset({"manual", "on_start", "periodic"})


@dataclass(frozen=True, slots=True)
class SourceRefreshSchedule:
    mode: str = "manual"
    interval_minutes: int = 60

    def __post_init__(self):
        if type(self.mode) is not str or self.mode not in _MODES:
            raise ValueError("invalid source refresh mode")
        if (
            type(self.interval_minutes) is not int
            or not 15 <= self.interval_minutes <= 10080
        ):
            raise ValueError("invalid source refresh interval")


def _key(source_id: str) -> str:
    if type(source_id) is not str or not _SOURCE_ID.fullmatch(source_id):
        raise ValueError("invalid source refresh key")
    return "sync.source.refresh.policy." + source_id


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate policy key")
        value[key] = item
    return value


def decode_source_refresh_schedule(raw: Any) -> SourceRefreshSchedule:
    """Fail closed for old, corrupt or mixed-phase settings."""
    if type(raw) is not str or not 1 <= len(raw) <= 512:
        return SourceRefreshSchedule()
    try:
        payload = json.loads(raw, object_pairs_hook=_unique_object)
        if (
            type(payload) is not dict
            or set(payload) != {"schema", "mode", "interval_minutes"}
            or payload["schema"] != _SCHEMA
        ):
            return SourceRefreshSchedule()
        return SourceRefreshSchedule(
            mode=payload["mode"], interval_minutes=payload["interval_minutes"],
        )
    except (ValueError, TypeError, OverflowError, RecursionError):
        return SourceRefreshSchedule()


def load_source_refresh_schedule(storage, source_id: str) -> SourceRefreshSchedule:
    """Passive read; never make a legacy global flag grant new permissions."""
    key = _key(source_id)
    try:
        return decode_source_refresh_schedule(storage.metadata_get(key))
    except Exception:
        return SourceRefreshSchedule()


def save_source_refresh_schedule(
    storage, source_id: str, *, mode: str, interval_minutes: int = 60,
) -> SourceRefreshSchedule:
    """Strict opt-in write; caller must first authorize exact source ownership.

    Persist only enumerated phase/mode information, no provider identifiers,
    tokens or profile names. There is no automatic acquisition option here.
    """
    key = _key(source_id)
    value = SourceRefreshSchedule(mode=mode, interval_minutes=interval_minutes)
    storage.metadata_set(key, json.dumps({
        "schema": _SCHEMA, "mode": value.mode,
        "interval_minutes": value.interval_minutes,
    }, sort_keys=True, separators=(",", ":")))
    return value


def _last_attempt_utc(value) -> datetime | None:
    if type(value) is str and value == "":
        return None
    if type(value) is not str or not 20 <= len(value) <= 35:
        raise ValueError("invalid last-attempt time")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or not 2000 <= parsed.year <= 2100:
        raise ValueError("invalid last-attempt time")
    return parsed.astimezone(timezone.utc)


def source_refresh_due(
    policy: SourceRefreshSchedule, *, trigger: str, now: datetime,
    last_attempt: str = "", phase_status: str = "idle",
    enabled: bool, connected: bool, capable: bool,
    already_requested_this_startup: bool = False,
) -> bool:
    """Pure admission decision for discover/rights, never materialization.

    A future/invalid timestamp is NOT an excuse to sync again after a
    rollback. Repeated startup invocations and busy phases are suppressed.
    The executor must still revalidate ownership and permissions at dispatch.
    """
    if (
        type(enabled) is not bool or enabled is not True
        or type(connected) is not bool or connected is not True
        or type(capable) is not bool or capable is not True
        or type(already_requested_this_startup) is not bool
        or type(phase_status) is not str
        or phase_status not in {"idle", "success", "partial", "failed"}
        or type(trigger) is not str
        or not isinstance(policy, SourceRefreshSchedule)
        or not isinstance(now, datetime) or now.tzinfo is None
    ):
        return False
    if policy.mode == "manual":
        return False
    if policy.mode == "on_start" and (
        trigger != "startup" or already_requested_this_startup
    ):
        return False
    if policy.mode == "periodic" and trigger != "periodic":
        return False
    try:
        previous = _last_attempt_utc(last_attempt)
        if previous is None:
            return True
        elapsed = (now.astimezone(timezone.utc) - previous).total_seconds()
        return elapsed >= policy.interval_minutes * 60
    except (ValueError, TypeError, OverflowError):
        return False


def inventory_source_eligible(service: Any, account: Any) -> bool:
    """Admit an inventory refresh using the SAME private source binding as Sync.

    Scheduling is a passive observation only: it never grants acquisition.
    Keep its explicit persisted enablement check and strict nonempty schedule
    coordinates, then delegate profile/account/session ownership to the
    provider-neutral lease validator shared with source Sync and opening.
    """
    try:
        scope = (
            account.identity_id, account.provider_id,
            account.credential_id, account.unique_id,
        )
        if not all(
            type(value) is str and value.strip() and "\x00" not in value
            for value in scope
        ):
            return False

        # An absent legacy schedule switch is not new consent. A malformed
        # explicit preference must deny the automatic private network read.
        from ..brokers import SynchronizationBrokerImpl
        source_id = SynchronizationBrokerImpl._source_id(account)
        enabled = service.storage.metadata_get("sync.source.enabled." + source_id)
        if enabled is not None and enabled not in {"1", "true", "yes", "on"}:
            return False

        sessions = [
            session for session in service._sessions.values()
            if getattr(session, "provider_id", None) == scope[1]
            and getattr(session, "credential_id", None) == scope[2]
        ]
        if len(sessions) != 1:
            return False
        session = sessions[0]
        if getattr(getattr(session, "capabilities", None),
                   "personal_library", None) is not True:
            return False

        from .private_inventory_binding import private_inventory_binding_current
        from .source_context import source_context_epoch
        return private_inventory_binding_current(
            service, account, session, scope=scope,
            epoch=source_context_epoch(service),
        )
    except Exception:
        # Never grant a scheduled private read when persistence or the live
        # source context cannot be proved.
        return False


def kobo_inventory_source_eligible(service: Any, account: Any) -> bool:
    """Compatibility name; the shared guard owns the binding decision."""
    return (getattr(account, "provider_id", None) == "kobo"
            and inventory_source_eligible(service, account))


def numilog_inventory_source_eligible(service: Any, account: Any) -> bool:
    """Compatibility name; the shared guard owns the binding decision."""
    return (getattr(account, "provider_id", None) == "numilog"
            and inventory_source_eligible(service, account))

