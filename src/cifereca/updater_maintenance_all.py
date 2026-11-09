"""#5069: one explicit plan/apply across the updater-owned history stores.

This is orchestration of the existing per-store executors, not a second
retention policy. A source object not present in the exact acknowledged
per-store plan can never become authorized mid-sweep.
"""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any, Callable

_SHA = re.compile(r"[a-f0-9]{64}\Z")
_SCHEMA = "cifereca.updater.maintenance-all/1"


def _stores(runtime: Path, packages: Path, dev_backups: Path,
            core_backups: Path, *, keep: int):
    from .updater_core_release_maintenance import core_release_maintenance
    from .updater_transaction_maintenance import transaction_maintenance
    from .updater_plugin_maintenance import plugin_history_maintenance
    from .updater_dev_backup_maintenance import dev_backup_maintenance
    from .updater_core_backup_maintenance import core_backup_maintenance
    from .updater_staging_maintenance import staging_maintenance
    # Order matters: a release must disappear before its journal can be
    # discarded, and a journal before the historical plugin version it pins.
    return (
        ("releases", core_release_maintenance, (runtime,), {"keep": keep},
         "candidate_releases"),
        ("transactions", transaction_maintenance, (runtime,), {},
         "candidate_transactions"),
        ("plugins", plugin_history_maintenance, (runtime, packages), {},
         "candidate_versions"),
        ("dev_backups", dev_backup_maintenance, (runtime, dev_backups), {},
         "candidate_backups"),
        ("core_backups", core_backup_maintenance, (runtime, core_backups), {},
         "candidate_backups"),
        ("staging", staging_maintenance,
         (runtime, packages, core_backups), {}, "candidate_stages"),
    )


def _plans(stores, now: float | None) -> dict[str, dict[str, Any]]:
    results = {}
    for name, executor, args, options, count_key in stores:
        result = executor(*args, **options,
                          **({} if now is None else {"now": now}))
        if (type(result) is not dict
                or result.get("mode") != "plan"
                or type(result.get(count_key)) is not int
                or result[count_key] < 0
                or type(result.get("acknowledgement_sha256")) is not str
                or _SHA.fullmatch(result["acknowledgement_sha256"]) is None):
            raise ValueError("maintenance_all_unqualified_plan")
        results[name] = {
            "candidates": result[count_key],
            "sha256": result["acknowledgement_sha256"],
        }
    return results


def _digest(runtime: Path, packages: Path, dev_backups: Path,
            core_backups: Path, keep: int, plans: dict) -> str:
    payload = {
        "schema": _SCHEMA,
        "roots": tuple(str(p.absolute()) for p in
                       (runtime, packages, dev_backups, core_backups)),
        "keep": keep, "plans": plans,
    }
    return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"))
                  .encode()).hexdigest()


def maintenance_all(
    runtime_root: Path, package_root: Path, dev_backup_root: Path,
    core_backup_root: Path, *, apply: bool = False,
    acknowledge: str | None = None, now: float | None = None,
    keep: int = 2,
) -> dict[str, Any]:
    """Plan a complete bounded sweep, or apply an exact user-acknowledged plan.

    One updater/package lease is held through the full sweep. A store whose
    dependencies change as preceding stores are deleted is deferred to the
    next invocation; it does not inherit permission from earlier candidates.
    """
    from . import updater
    if type(keep) is not int or keep < 2:
        raise ValueError("maintenance_all_invalid_minimum")
    runtime, packages, dev_backups, core_backups = map(
        Path, (runtime_root, package_root, dev_backup_root, core_backup_root),
    )
    stores = _stores(runtime, packages, dev_backups, core_backups, keep=keep)
    if not apply:
        plans = _plans(stores, now)
        return {
            "schema": _SCHEMA, "mode": "plan", "safe_to_apply": False,
            "acknowledgement_sha256": _digest(
                runtime, packages, dev_backups, core_backups, keep, plans),
            "stores": {name: {"candidates": item["candidates"]}
                       for name, item in plans.items()},
        }
    if type(acknowledge) is not str or _SHA.fullmatch(acknowledge) is None:
        raise ValueError("maintenance_all_acknowledgement_required")
    with updater._exclusive_updater_lock(runtime):
        with updater._trusted_package_lease():
            plans = _plans(stores, now)
            expected = _digest(
                runtime, packages, dev_backups, core_backups, keep, plans)
            if acknowledge != expected:
                raise ValueError("maintenance_all_plan_changed")
            outcome = {}
            for name, executor, args, options, count_key in stores:
                initial = plans[name]
                # Replan after earlier deletions, retaining the full shared
                # lease. Never dispose a newly eligible unapproved object.
                try:
                    fresh = executor(*args, **options,
                                     **({} if now is None else {"now": now}))
                    if (fresh.get("acknowledgement_sha256") != initial["sha256"]
                            or fresh.get(count_key) != initial["candidates"]):
                        outcome[name] = {"status": "deferred", "removed": 0,
                                         "reason": "plan_changed"}
                        continue
                    if not initial["candidates"]:
                        outcome[name] = {"status": "complete", "removed": 0}
                        continue
                    result = executor(
                        *args, **options, apply=True,
                        acknowledge=initial["sha256"],
                        **({} if now is None else {"now": now}),
                    )
                    if type(result.get("removed")) is not int:
                        raise ValueError("maintenance_all_unqualified_result")
                    outcome[name] = {
                        "status": "complete", "removed": result["removed"],
                    }
                except (ValueError, OSError, updater.UpdateError):
                    outcome[name] = {"status": "deferred", "removed": 0,
                                     "reason": "store_unqualified"}
            return {"schema": _SCHEMA, "mode": "apply",
                    "stores": outcome,
                    "removed": sum(x["removed"] for x in outcome.values())}


__all__ = ["maintenance_all"]
