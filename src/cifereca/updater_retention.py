"""P0 #5069 B1 — pure, fail-closed updater retention policy.

This module never walks or mutates a filesystem. Enumerators must first prove
updater ownership, timestamps and complete cross-store references. This is
not a garbage collector and must never be used to purge user data.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import math
from typing import Iterable


WINDOW_SECONDS = 24 * 60 * 60
MINIMUM_GENERATIONS = 2


class RetentionPolicyError(ValueError):
    """Unsafe or incomplete retention inventory (no removal authorized)."""


@dataclass(frozen=True, slots=True)
class RetentionObject:
    object_id: str
    store: str
    unit: str
    generation: str
    installed_at: float | int | None
    owned_by_updater: bool = False
    pinned: bool = False
    active: bool = False
    references: tuple[str, ...] = ()


def plan_retention(
    objects: Iterable[RetentionObject], *, now: float,
    minimum: int = MINIMUM_GENERATIONS,
    window_seconds: int = WINDOW_SECONDS,
) -> dict[str, object]:
    """Return a deterministic *plan*, never an authorization to delete.

    Keep the union of (<window), >=minimum most recent distinct generations
    per (store, unit), active and explicit pins, all unowned/undated/future
    objects, and the transitive dependencies of everything retained.

    Missing references block *all* candidates, rather than silently dropping
    the reference. Unknown dates and ownership cannot authorize a removal.
    Exactly 24h is outside the time-window, but still subject to all pins.
    """
    if (type(now) not in (int, float) or not math.isfinite(now)
            or type(minimum) is not int or minimum < MINIMUM_GENERATIONS
            or type(window_seconds) is not int or window_seconds <= 0):
        raise RetentionPolicyError("invalid_retention_policy")
    rows = list(objects)
    by_id: dict[str, RetentionObject] = {}
    reasons: dict[str, set[str]] = {}
    groups: dict[tuple[str, str], dict[str, list[RetentionObject]]] = defaultdict(
        lambda: defaultdict(list),
    )
    for row in rows:
        if type(row) is not RetentionObject:
            raise RetentionPolicyError("invalid_retention_object")
        if (any(type(value) is not str or not value
                for value in (row.object_id, row.store, row.unit, row.generation))
                or row.object_id in by_id
                or type(row.owned_by_updater) is not bool
                or type(row.active) is not bool or type(row.pinned) is not bool
                or type(row.references) is not tuple
                or any(type(ref) is not str or not ref for ref in row.references)):
            raise RetentionPolicyError("invalid_or_duplicate_retention_identity")
        by_id[row.object_id] = row
        reasons[row.object_id] = set()
        groups[(row.store, row.unit)][row.generation].append(row)
        if not row.owned_by_updater:
            reasons[row.object_id].add("unowned")
        if row.pinned:
            reasons[row.object_id].add("pinned")
        if row.active:
            reasons[row.object_id].add("active")
        if (type(row.installed_at) not in (int, float)
                or not math.isfinite(row.installed_at)):
            reasons[row.object_id].add("unknown_timestamp")
        elif row.installed_at > now:
            reasons[row.object_id].add("future_timestamp")
        elif now - row.installed_at < window_seconds:
            reasons[row.object_id].add("within_24h")

    for key, generations in groups.items():
        # The "2" counts distinct generations, not multiple files belonging
        # to the same generation. Ties break deterministically on generation.
        def generation_timestamp(group: list[RetentionObject]) -> float:
            candidates = [
                float(row.installed_at) for row in group
                if type(row.installed_at) in (int, float)
                and math.isfinite(row.installed_at)
            ]
            return max(candidates) if candidates else float("-inf")

        ordered = sorted(
            generations.items(),
            key=lambda pair: (generation_timestamp(pair[1]), pair[0]),
            reverse=True,
        )
        for _generation, members in ordered[:minimum]:
            for row in members:
                reasons[row.object_id].add("minimum_generations")
        # A generation is an atomic retention unit: a young/pinned/active
        # member must never make its older siblings deletion candidates.
        for members in generations.values():
            if any(reasons[row.object_id] for row in members):
                for row in members:
                    reasons[row.object_id].add("generation_retained")

    missing_references = sorted({
        ref for row in rows for ref in row.references if ref not in by_id
    })
    if missing_references:
        # Do not echo IDs/paths from a potentially private inventory.
        return {
            "schema": "cifereca.updater.retention-plan/1",
            "safe_to_apply": False,
            "blockers": ["incomplete_reference_graph"],
            "total": len(rows), "candidates": 0,
            "objects": [
                {"object_id": row.object_id, "decision": "retain",
                 "reasons": sorted(reasons[row.object_id] | {"unresolved_reference"})}
                for row in sorted(rows, key=lambda obj: obj.object_id)
            ],
        }

    # A pinned transaction/release protects every transitively referenced
    # snapshot, plugin version and backup. Cycles terminate at the visited set.
    queue = [key for key in sorted(by_id) if reasons[key]]
    expanded: set[str] = set()
    while queue:
        key = queue.pop()
        if key in expanded:
            continue
        expanded.add(key)
        current = by_id[key]
        # A referenced file can activate a whole generation *after* the
        # initial 24h/minimum pass. Expand siblings and references together
        # until the closure stabilizes; never plan a partial generation.
        for sibling in groups[(current.store, current.unit)][current.generation]:
            reasons[sibling.object_id].add("generation_retained")
            queue.append(sibling.object_id)
        for dependency in current.references:
            reasons[dependency].add("referenced")
            queue.append(dependency)

    ordered_rows = sorted(rows, key=lambda item: item.object_id)
    decisions = [
        {"object_id": row.object_id,
         "decision": "retain" if reasons[row.object_id] else "candidate",
         "reasons": sorted(reasons[row.object_id])}
        for row in ordered_rows
    ]
    return {
        "schema": "cifereca.updater.retention-plan/1",
        "safe_to_apply": False,  # Requires independent ownership and TOCTOU checks.
        "blockers": ["inventory_only_no_gc_implementation"],
        "total": len(rows),
        "candidates": sum(entry["decision"] == "candidate" for entry in decisions),
        "objects": decisions,
    }
