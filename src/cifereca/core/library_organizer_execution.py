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


def execute_reviewed_rename(scope, page, accepted, *, shared_lock, confirmed=False):
    """Isolated recipe for one exact rename, never keeper/classification.

    Delegate all writes to the existing maintenance broker. A postcheck
    failure reports recovery required; it never invokes a second writer or
    attempts speculative rollback after the commit boundary.
    """
    from ..artifact_runtime import LibraryMaintenanceBrokerImpl
    from ..plugin_api import LibraryMutationRequest

    if (confirmed is not True or type(accepted) is not OrganizerDryRunPlan
            or accepted.scope is not scope or accepted.confirmed is not True
            or accepted.executable is not False or len(accepted.entries) != 1
            or accepted.entries[0].operation != "rename"
            or accepted.entries[0].state != "review_only"
            or not _lock_ok(shared_lock)
            or not _execution_fence_current(scope, shared_lock)):
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
