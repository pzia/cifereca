"""#1498: overlapping Core views cannot invalidate or misdirect a cleanup batch.

Every broker is synthetic; Core's destructive operation is only recorded.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

from cifereca.plugin_api import ArtifactPlacementRef, MaintenanceContext
from cifereca_library_audits import DuplicateCleanupTool
from tests.test_duplicate_cleanup_distinct_views_p1 import A, B, Context, Control


def placement(identifier, artifact=A):
    return ArtifactPlacementRef(identifier, artifact, "current")


def view(*placements, book="shared-book"):
    return NS(book=NS(book_id=book), artifacts=(), placements=placements)


def preview(views):
    context = Context(views)
    tool = DuplicateCleanupTool().activate(context)
    plan = tool.plan(MaintenanceContext(scope="library", identity_id="current"))
    return context, tool, plan


def test_overlapping_snapshots_dedupe_duplicate_and_preserve_extra_placement():
    context, tool, plan = preview({
        "artifact-one": view(placement("keep"), placement("second")),
        "artifact-two": view(
            placement("keep"), placement("second"), placement("third")
        ),
    })
    assert plan.facts == {
        "pairs": (("second", "keep"), ("third", "keep")),
        "read_failures": 0,
    }
    assert plan.changes == (
        "Supprimer le doublon n°1", "Supprimer le doublon n°2",
    )
    result = tool.run(plan, Control())
    assert result.ok and result.changed == 2
    assert [(r.placement_id, r.keep_placement_id) for r in context.requests] == [
        ("second", "keep"), ("third", "keep"),
    ]


def test_conflicting_keepers_are_quarantined_but_independent_pair_survives():
    context, tool, plan = preview({
        "a": view(placement("a"), placement("x")),
        "b": view(placement("b"), placement("x")),
        "safe": view(placement("safe-0", B), placement("safe-1", B)),
    })
    assert plan.facts["pairs"] == (("safe-1", "safe-0"),)
    assert plan.facts["read_failures"] == 1
    assert plan.changes == ("Supprimer le doublon n°1",)
    result = tool.run(plan, Control())
    assert not result.ok and result.changed == 1
    assert [(r.placement_id, r.keep_placement_id) for r in context.requests] == [
        ("safe-1", "safe-0"),
    ]
    exposed = plan.summary + " ".join(plan.changes + plan.warnings)
    assert " x " not in exposed and "PRIVATE" not in exposed


def test_deleted_keeper_in_another_view_fails_closed_for_related_chain():
    context, tool, plan = preview({
        "a": view(placement("a"), placement("b")),
        "b": view(placement("b"), placement("c")),
        "safe": view(placement("safe-0", B), placement("safe-1", B)),
    })
    assert plan.facts["pairs"] == (("safe-1", "safe-0"),)
    assert plan.facts["read_failures"] == 1
    result = tool.run(plan, Control())
    assert result.changed == 1 and not result.ok
    assert len(context.requests) == 1


def test_same_overlapping_pair_twice_is_not_an_error():
    context, tool, plan = preview({
        "a": view(placement("a"), placement("b")),
        "b": view(placement("a"), placement("b")),
    })
    assert plan.facts["pairs"] == (("b", "a"),)
    assert plan.facts["read_failures"] == 0
    result = tool.run(plan, Control())
    assert result.ok and result.changed == 1
    assert len(context.requests) == 1
