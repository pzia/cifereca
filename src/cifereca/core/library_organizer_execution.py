"""#3176 isolated single-rename receipt using the existing Core writer.

No production activation: the independent consumer/maintenance fence has
not been qualified. Neither a confirmed dry-run nor a caller flag lifts it.
"""
from dataclasses import replace
from pathlib import Path

from .library_organizer_plan import OrganizerDryRunPlan, confirm_organizer_dry_run
from .canonical_recovery import _lock_ok
from .library_sqlite_freshness import sqlite_source_stamp


def _execution_fence_current(scope, lock):
    return False


def execute_reviewed_rename(scope, page, accepted, *, shared_lock, confirmed=False,
                            still_current=lambda: True):
    """Isolated recipe for one exact rename, never keeper/classification.

    Delegate all writes to the existing maintenance broker. A postcheck
    failure reports recovery required; it never invokes a second writer or
    attempts speculative rollback after the commit boundary.
    """
    from ..artifact_runtime import LibraryMaintenanceBrokerImpl
    from ..plugin_api import LibraryMutationRequest

    try:
        permitted = (confirmed is True and callable(still_current)
                     and still_current() is True
                     and type(accepted) is OrganizerDryRunPlan
                     and accepted.scope is scope and accepted.confirmed is True
                     and accepted.executable is False and len(accepted.entries) == 1
                     and accepted.entries[0].operation == "rename"
                     and accepted.entries[0].state == "review_only"
                     and _lock_ok(shared_lock)
                     and _execution_fence_current(scope, shared_lock))
    except Exception:
        # An exception from a revoked view/lock/fence never authorizes a write.
        return {"status": "blocked", "changed": 0}
    if not permitted:
        return {"status": "blocked", "changed": 0}
    entry = accepted.entries[0]
    invoked = False
    try:
        fresh = confirm_organizer_dry_run(
            scope, page, replace(accepted, confirmed=False), confirmed=True)
        if fresh != accepted:
            return {"status": "blocked", "changed": 0}
        old = scope.root / scope.directory / entry.current
        new = scope.root / scope.directory / entry.proposed
        if (old.parent != new.parent or not old.is_file()
                or new.exists() or new.is_symlink()
                or scope.storage.placement_for_path(new) is not None):
            return {"status": "blocked", "changed": 0}
        broker = LibraryMaintenanceBrokerImpl(scope.service)
        plan = broker.plan(LibraryMutationRequest(
            "normalize_filename", placement_id=entry.placement_id))
        row = scope.storage.placement_get(entry.placement_id)
        if (not row or row.get("identity_id") != scope.identity
                or row.get("path") != str(old)
                or plan.operation != "normalize_filename"
                or plan.destructive is not False
                or plan.changes != (old.name, new.name)
                or plan.facts.get("new_filename") != new.name
                or not scope.bound()
                or sqlite_source_stamp(scope.database) != accepted.anchor.stamp
                or still_current() is not True
                or not _lock_ok(shared_lock)
                or not _execution_fence_current(scope, shared_lock)):
            return {"status": "blocked", "changed": 0}
        invoked = True
        result = broker.run(plan)
        current = scope.storage.placement_get(entry.placement_id)
        if (not current or current.get("path") != str(new)
                or current.get("artifact_id") != row.get("artifact_id")
                or current.get("identity_id") != scope.identity
                or current.get("provider_id") != row.get("provider_id")
                or current.get("source_item_id") != row.get("source_item_id")
                or current.get("state") != row.get("state")
                or not new.is_file() or new.is_symlink()
                or old.exists() or old.is_symlink()
                or result.ok is not True or result.warnings or result.changed != 1):
            return {"status": "recovery_required", "changed": None}
        return {"status": "renamed", "changed": 1}
    except Exception:
        return {"status": "recovery_required" if invoked else "blocked",
                "changed": None if invoked else 0}


def admit_native_reviewed_rename(manager, resolved, page, *, service, identity_id,
                                 confirmed=False, view_current):
    """Core-only one-shot worker; explicit consent and exact mounted review.

    No lock or database work on the UI thread: call the admission inside the
    admitted worker. A missing independent fence still refuses all writes.
    """
    from threading import Lock
    from ..locking import InterProcessLock
    from .canonical_recovery import acquisition_lock_file
    from .library_organizer_broker import take_native_organizer_rename

    if confirmed is not True or not callable(view_current):
        return None
    try:
        if view_current() is not True:
            return None
        pending = take_native_organizer_rename(
            manager, resolved, page, service=service, identity_id=identity_id)
        if pending is None:
            return None
        scope, report, plan, current = pending
    except Exception:
        return None

    guard = Lock()
    used = False

    def live():
        try:
            return view_current() is True and current() is True
        except Exception:
            return False

    def work():
        nonlocal used
        with guard:
            if used:
                return {"status": "blocked", "changed": 0}
            used = True
        if not live():
            return {"status": "blocked", "changed": 0}
        try:
            # Do not create/truncate an acquisition lock during maintenance.
            with InterProcessLock(acquisition_lock_file(create=False), existing_nofollow=True) as lock:
                def current_lock():
                    return live() and lock.strict_current()
                if not current_lock():
                    return {"status": "blocked", "changed": 0}
                return execute_reviewed_rename(
                    scope, report, plan, shared_lock=lock, confirmed=True,
                    still_current=current_lock)
        except Exception:
            return {"status": "blocked", "changed": 0}
    return work if live() else None
