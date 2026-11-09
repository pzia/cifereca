"""P0 #5069: supervised cleanup of existing Core releases without deploying.

The exact read-only retention plan grants a bounded allowlist to the existing
post-commit _gc implementation; no parallel release GC algorithm is created.
"""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import re
import time

from .updater_retention_inventory import _name_id, inspect_core_transaction_history

_SHA = re.compile(r"[a-f0-9]{64}\Z")
_SCHEMA = "cifereca.updater.core-release-maintenance/1"
_ALLOWED_BLOCKERS = {"partial_store_coverage", "inventory_only_no_gc_implementation"}


def _plan(root: Path, *, now: float, keep: int) -> tuple[tuple[str, ...], str]:
    from . import updater
    report = inspect_core_transaction_history(root, now=now)
    plan = report.get("plan")
    if (set(report.get("blockers", ())) - _ALLOWED_BLOCKERS
            or type(plan) is not dict or type(plan.get("objects")) is not list):
        raise ValueError("core_release_gc_inventory_unqualified")
    rows = plan["objects"]
    candidates = {
        row["object_id"] for row in rows
        if type(row) is dict
        and row.get("decision") == "candidate"
        and type(row.get("object_id")) is str
        and row["object_id"].startswith("release:")
        and row.get("reasons") == []
    }
    if root.is_symlink() or (root.exists() and not root.is_dir()):
        raise ValueError("core_release_gc_unsafe_runtime")
    releases = updater._releases(root)
    keep = max(updater.MIN_RETAINED_RELEASES, keep)
    ordered = sorted(
        releases,
        key=lambda item: (
            updater._release_installed_at(item) is not None,
            updater._release_installed_at(item) or 0,
            item.name,
        ),
        reverse=True,
    )
    retained_by_floor = {item.name for item in ordered[:keep]}
    selected = []
    for release in releases:
        if (_name_id("release", release.name) in candidates
                and release.name not in retained_by_floor):
            selected.append(release.name)
    if len(selected) > len(candidates):
        raise ValueError("core_release_gc_plan_inconsistent")
    names = tuple(sorted(selected))
    proof = sha256(
        (str(root) + "\n" + str(keep) + "\n" + "\n".join(names)).encode()
    ).hexdigest()
    return names, proof


def core_release_maintenance(
    runtime_root: Path, *, now: float | None = None,
    keep: int = 2, apply: bool = False,
    acknowledge: str | None = None,
) -> dict:
    from . import updater
    root = Path(runtime_root)
    instant = time.time() if now is None else now
    if type(keep) is not int or keep < 2:
        raise ValueError("core_release_gc_invalid_floor")
    if not apply:
        names, proof = _plan(root, now=instant, keep=keep)
        return {
            "schema": _SCHEMA, "mode": "plan", "safe_to_apply": False,
            "candidate_releases": len(names), "minimum_generations": keep,
            "acknowledgement_sha256": proof,
        }
    if type(acknowledge) is not str or _SHA.fullmatch(acknowledge) is None:
        raise ValueError("core_release_gc_acknowledgement_required")
    with updater._exclusive_updater_lock(root):
        with updater._trusted_package_lease():
            names, proof = _plan(root, now=instant, keep=keep)
            if proof != acknowledge:
                raise ValueError("core_release_gc_plan_changed")
            if not names:
                return {"schema": _SCHEMA, "mode": "apply", "removed": 0}
            result = updater._gc(
                root, keep, approved_release_names=frozenset(names),
            )
            return {
                "schema": _SCHEMA, "mode": "apply",
                "removed": result["removed"], "deferred": result["deferred"],
            }


__all__ = ["core_release_maintenance"]
