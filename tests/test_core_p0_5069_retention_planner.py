"""P0 #5069 B1: pure 24h+2 policy, no filesystem or destructive calls."""
from __future__ import annotations

import pytest

from cifereca.updater_retention import (
    MINIMUM_GENERATIONS, WINDOW_SECONDS, RetentionObject,
    RetentionPolicyError, plan_retention,
)


NOW = 1_000_000.0


def obj(index, age, *, store="releases", unit="core", **kwargs):
    return RetentionObject(
        object_id=f"{store}-{unit}-{index}",
        store=store, unit=unit, generation=str(index),
        installed_at=None if age is None else NOW - age,
        owned_by_updater=True, **kwargs,
    )


def decisions(plan):
    return {r["object_id"]: r for r in plan["objects"]}


def test_seven_deployments_within_24h_are_all_kept_without_cap():
    objects = [obj(i, i * 100) for i in range(7)]
    plan = plan_retention(objects, now=NOW)
    assert plan["total"] == 7 and plan["candidates"] == 0
    assert all(r["decision"] == "retain" for r in plan["objects"])
    assert any("within_24h" in r["reasons"] for r in plan["objects"])


def test_older_than_24h_keeps_exactly_two_distinct_last_generations():
    objects = [obj(i, WINDOW_SECONDS + 1 + i * 100) for i in range(7)]
    p = plan_retention(objects, now=NOW)
    rows = decisions(p)
    assert p["candidates"] == 5
    assert [key for key, row in rows.items() if row["decision"] == "retain"] == [
        objects[0].object_id, objects[1].object_id,
    ]


def test_24h_exact_boundary_is_not_inside_window():
    objects = [obj(0, WINDOW_SECONDS), obj(1, WINDOW_SECONDS + 1),
               obj(2, WINDOW_SECONDS + 2)]
    rows = decisions(plan_retention(objects, now=NOW))
    assert "within_24h" not in rows[objects[0].object_id]["reasons"]
    assert rows[objects[2].object_id]["decision"] == "candidate"


def test_same_policy_applies_independently_to_plugin_packages_and_transactions():
    items = [
        obj(i, WINDOW_SECONDS + i + 1, store="plugin_versions", unit="org.one")
        for i in range(4)
    ] + [
        obj(i, WINDOW_SECONDS + i + 1, store="plugin_versions", unit="org.two")
        for i in range(5)
    ] + [
        obj(i, WINDOW_SECONDS + i + 1, store="transactions", unit="updater")
        for i in range(6)
    ]
    plan = plan_retention(items, now=NOW)
    assert plan["total"] == 15
    assert plan["candidates"] == (4 - 2) + (5 - 2) + (6 - 2)
    assert all(
        len([r for r in plan["objects"] if r["object_id"].startswith(
            f"plugin_versions-{unit}-") and r["decision"] == "retain"]) == 2
        for unit in ("org.one", "org.two")
    )


def test_pins_and_transitive_dependencies_retained_beyond_minimum():
    backup = obj(99, 48 * 3600, store="backups", unit="updater")
    snapshot = obj(98, 48 * 3600, store="snapshots", unit="tx", references=(backup.object_id,))
    transaction = obj(97, 48 * 3600, store="transactions", unit="updater",
                      pinned=True, references=(snapshot.object_id,))
    other = [obj(i, WINDOW_SECONDS + i, store="transactions", unit="updater")
             for i in range(5)]
    p = plan_retention([*other, transaction, snapshot, backup], now=NOW)
    rows = decisions(p)
    assert rows[transaction.object_id]["decision"] == "retain"
    assert "referenced" in rows[snapshot.object_id]["reasons"]
    assert "referenced" in rows[backup.object_id]["reasons"]
    assert p["safe_to_apply"] is False


def test_future_unknown_unowned_and_active_are_conservatively_retained():
    rows = [
        obj(0, -1000),
        obj(1, None),
        obj(2, WINDOW_SECONDS * 3, active=True),
        RetentionObject("personal", "backups", "unowned", "old",
                        NOW - 100 * WINDOW_SECONDS),
    ]
    p = decisions(plan_retention(rows, now=NOW))
    assert all(x["decision"] == "retain" for x in p.values())
    assert "future_timestamp" in p[rows[0].object_id]["reasons"]
    assert "unknown_timestamp" in p[rows[1].object_id]["reasons"]
    assert "unowned" in p["personal"]["reasons"]


def test_missing_dependency_fails_closed_even_if_other_entries_aged_out():
    rows = [obj(i, WINDOW_SECONDS * 3 + i) for i in range(5)]
    culprit = obj(90, WINDOW_SECONDS * 3, store="snapshots", unit="updater",
                  references=("missing-proof",))
    p = plan_retention([*rows, culprit], now=NOW)
    assert p["safe_to_apply"] is False
    assert p["blockers"] == ["incomplete_reference_graph"]
    assert p["candidates"] == 0
    assert all(x["decision"] == "retain" for x in p["objects"])


def test_invalid_policy_and_duplicate_ids_are_refused():
    with pytest.raises(RetentionPolicyError):
        plan_retention([obj(0, 0)], now=NOW, minimum=1)
    with pytest.raises(RetentionPolicyError):
        plan_retention([obj(0, 0), obj(0, 1)], now=NOW)
    with pytest.raises(RetentionPolicyError):
        plan_retention([obj(0, 0)], now=float("nan"))
    assert MINIMUM_GENERATIONS == 2


def test_cycle_in_reference_graph_finishes_and_preserves_both():
    left = obj(0, WINDOW_SECONDS * 2, store="snapshots", unit="a",
               pinned=True, references=("snapshots-b-0",))
    right = obj(0, WINDOW_SECONDS * 3, store="snapshots", unit="b",
                references=(left.object_id,))
    p = plan_retention([left, right], now=NOW)
    assert p["candidates"] == 0
    assert "referenced" in decisions(p)[right.object_id]["reasons"]


def test_generation_is_atomic_even_with_mixed_timestamps():
    same_generation = [
        RetentionObject("young", "snapshots", "tx", "G", NOW - 60, owned_by_updater=True),
        RetentionObject("old-part", "snapshots", "tx", "G",
                        NOW - 4 * WINDOW_SECONDS, owned_by_updater=True),
    ]
    other_generations = [
        RetentionObject(f"new-{i}", "snapshots", "tx", f"new-G-{i}",
                        NOW - (WINDOW_SECONDS + i),
                        owned_by_updater=True)
        for i in range(3)
    ]
    p = decisions(plan_retention([*same_generation, *other_generations], now=NOW))
    assert p["old-part"]["decision"] == "retain"
    assert "generation_retained" in p["old-part"]["reasons"]
    assert p["new-2"]["decision"] == "candidate"


def test_referenced_member_pins_entire_generation_after_transitive_closure():
    # Two newer snapshot generations occupy the min2; a third old
    # generation is retained *only* because a pinned transaction cites one
    # of its two file-level members.
    young1 = RetentionObject("new-1", "snapshots", "tx", "N1",
                             NOW - 100_000, owned_by_updater=True)
    young2 = RetentionObject("new-2", "snapshots", "tx", "N2",
                             NOW - 110_000, owned_by_updater=True)
    referenced = RetentionObject("old-referenced", "snapshots", "tx", "OLD",
                                  NOW - 200_000, owned_by_updater=True)
    sibling = RetentionObject("old-sibling", "snapshots", "tx", "OLD",
                               NOW - 200_000, owned_by_updater=True)
    tx = RetentionObject("pin-tx", "transactions", "updater", "TX",
                         NOW - 200_000, owned_by_updater=True,
                         pinned=True, references=("old-referenced",))
    rows = decisions(plan_retention(
        [young1, young2, referenced, sibling, tx], now=NOW,
    ))
    assert rows["old-referenced"]["decision"] == "retain"
    assert rows["old-sibling"]["decision"] == "retain"
    assert "generation_retained" in rows["old-sibling"]["reasons"]
