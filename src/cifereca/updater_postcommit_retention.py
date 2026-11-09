"""#5069: apply existing store policies after a successful Core update.

The transactional Core GC has already run. This ordered best-effort sweep
reuses each store's implemented 24h+2 plan/apply; it does not create new
retention rules or authorize cleanup outside updater-owned namespaces.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any


def postcommit_updater_retention(
    runtime_root: Path,
    package_root: Path,
    dev_backup_root: Path,
    core_backup_root: Path,
) -> dict[str, dict[str, Any]]:
    """A failed optional sweep never turns a committed update into rollback."""
    from .updater_transaction_maintenance import transaction_maintenance
    from .updater_plugin_maintenance import plugin_history_maintenance
    from .updater_dev_backup_maintenance import dev_backup_maintenance
    from .updater_core_backup_maintenance import core_backup_maintenance
    from .updater_staging_maintenance import staging_maintenance

    # Release GC ran first. Releasing journals before historical versions
    # lets the plugin planner recognize fewer retained rollback pins.
    stores = (
        ("transactions", transaction_maintenance,
         (runtime_root,), "candidate_transactions"),
        ("plugins", plugin_history_maintenance,
         (runtime_root, package_root), "candidate_versions"),
        ("dev_backups", dev_backup_maintenance,
         (runtime_root, dev_backup_root), "candidate_backups"),
        ("core_backups", core_backup_maintenance,
         (runtime_root, core_backup_root), "candidate_backups"),
        ("staging", staging_maintenance,
         (runtime_root, package_root, core_backup_root), "candidate_stages"),
    )
    results: dict[str, dict[str, Any]] = {}
    for store, executor, args, candidate_key in stores:
        try:
            plan = executor(*args)
            count = plan[candidate_key]
            if type(count) is not int or count < 0:
                raise ValueError("invalid postcommit plan")
            if not count:
                results[store] = {"status": "complete", "removed": 0}
                continue
            completed = executor(
                *args, apply=True,
                acknowledge=plan["acknowledgement_sha256"],
            )
            if type(completed.get("removed")) is not int:
                raise ValueError("unqualified postcommit result")
            results[store] = {
                "status": "complete", "removed": completed["removed"],
            }
        except Exception:
            # A post-success maintenance failure is a deferred cleanup,
            # never a reason to revert a Core/Plugin commit.
            results[store] = {"status": "deferred", "removed": None}
    return results


__all__ = ["postcommit_updater_retention"]
