"""#4496: private, explicit Core command for the cleanup preference.

The operation changes configuration only. An audit intent is persisted before
enabling; a post-save event attests completion. SQLite and config.json are
not one transaction: a missing completion is deliberately reported as such.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import secrets

from ..config import Config
from ..locking import InterProcessLock
from ..paths import acquisition_lock_file
from .destructive_cleanup_policy import (
    DESTRUCTIVE_CLEANUP_KEY, destructive_cleanup_enabled,
)


@dataclass(frozen=True, slots=True)
class CleanupSettingResult:
    enabled: bool
    changed: bool
    audit_complete: bool


def preserve_destructive_cleanup_setting(data, *, current):
    """Retain the live setting on ordinary Settings save/rollback.

    This owns only one key; unrelated maintenance extensions are preserved.
    The caller owns its ordinary config lock and data copy.
    """
    maintenance = data.get("maintenance")
    if type(maintenance) is not dict:
        maintenance = {}
        data["maintenance"] = maintenance
    maintenance["destructive_cleanup_enabled"] = destructive_cleanup_enabled(current)
    return data


def _audit(storage, *, request_id, before, after, outcome):
    storage.action_event_put(
        identity_id=None, action_type="maintenance.destructive_cleanup.setting",
        outcome=outcome, facts={
            "request_id": request_id, "previous_enabled": before,
            "requested_enabled": after,
        },
    )


def change_destructive_cleanup_setting(service, *, expected_config,
                                       expected_enabled, enabled, confirmed=False):
    """Caller must be trusted Core UI/CLI, never a plugin capability.

    Busy lock refuses the transition; retry explicitly once maintenance
    finishes. Revocation does not depend on a working audit sink, but its
    incomplete audit is visible. Activation requires a durable intent.
    No file, path, identity or provider data enters the audit events.
    """
    if (confirmed is not True or type(expected_enabled) is not bool
            or type(enabled) is not bool or type(expected_config) is not Config):
        raise ValueError("cleanup_setting_confirmation_required")
    if getattr(service, "config", None) is not expected_config:
        raise ValueError("cleanup_setting_stale")
    storage = service.storage
    with InterProcessLock(acquisition_lock_file()):
        cfg = service.config
        if (cfg is not expected_config
                or destructive_cleanup_enabled(cfg) is not expected_enabled):
            raise ValueError("cleanup_setting_stale")
        if enabled is expected_enabled:
            return CleanupSettingResult(enabled, False, True)
        # action_event_put has its own commit boundary; do not accidentally
        # commit work the caller has already staged on this worker.
        if storage._connect().in_transaction:
            raise ValueError("cleanup_setting_nested_transaction")
        request_id = secrets.token_hex(16)
        intent_logged = True
        try:
            _audit(storage, request_id=request_id, before=expected_enabled,
                   after=enabled, outcome="requested")
        except Exception:
            intent_logged = False
            if enabled:
                raise ValueError("cleanup_setting_audit_unavailable") from None

        previous = deepcopy(cfg.data)
        cfg.set(DESTRUCTIVE_CLEANUP_KEY, enabled)
        try:
            cfg.save()
        except Exception:
            cfg.data = previous
            # A failed save is never called a completed transition; the
            # caller must reload/reconcile if it cannot inspect the file.
            if intent_logged:
                try:
                    _audit(storage, request_id=request_id, before=expected_enabled,
                           after=enabled, outcome="save_failed")
                except Exception:
                    pass
            raise ValueError("cleanup_setting_save_failed") from None

        try:
            _audit(storage, request_id=request_id, before=expected_enabled,
                   after=enabled, outcome="applied")
            completed = intent_logged
        except Exception:
            completed = False
        return CleanupSettingResult(enabled, True, completed)
