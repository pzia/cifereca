"""#1498: different placement views of one book remain independently auditable.

Synthetic read brokers and mock Core mutations only: no filesystem deletes.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

from cifereca.plugin_api import (
    ArtifactPlacementRef, LibraryMutationResult, MaintenanceContext,
)
from cifereca_library_audits import DuplicateCleanupTool


A = "sha256:" + "a" * 64
B = "sha256:" + "b" * 64


def view(artifact, *, prefix, book_id="shared-book", identity="current"):
    return NS(
        book=NS(book_id=book_id),
        artifacts=(),  # Legacy read view without a declared artifact list.
        placements=(
            ArtifactPlacementRef(f"{prefix}-0-keep", artifact, identity),
            ArtifactPlacementRef(f"{prefix}-1-duplicate", artifact, identity),
        ),
    )


class Context:
    def __init__(self, views):
        self.views = views
        self.requests = []
        self.calls = []
        self.broker_names = []

    def broker(self, name):
        self.broker_names.append(name)
        if name == "metadata":
            return NS(library_entries=lambda **kwargs: [
                {"artifact_id": name, "title": "PRIVATE_BOOK"}
                for name in self.views
            ])
        if name == "library.read":
            def lookup(identifier):
                self.calls.append(identifier)
                return self.views[identifier]
            return NS(for_artifact=lookup)
        if name == "library.maintenance":
            def plan(request):
                self.requests.append(request)
                return request
            return NS(
                plan=plan,
                run=lambda request, control: LibraryMutationResult(
                    True, changed=1, summary="PRIVATE_CORE_MESSAGE",
                ),
            )
        raise AssertionError(name)


class Control:
    def __init__(self):
        self.events = []
    def check_cancelled(self):
        pass
    def report(self, **event):
        self.events.append(event)


def test_independent_views_with_same_book_id_are_not_silently_skipped():
    context = Context({A: view(A, prefix="a"), B: view(B, prefix="b")})
    tool = DuplicateCleanupTool().activate(context)
    plan = tool.plan(MaintenanceContext(scope="library", identity_id="current"))
    assert context.calls == [A, B]
    assert plan.facts["pairs"] == (
        ("a-1-duplicate", "a-0-keep"),
        ("b-1-duplicate", "b-0-keep"),
    )
    assert plan.facts["read_failures"] == 0
    assert plan.changes == (
        "Supprimer le doublon n°1",
        "Supprimer le doublon n°2",
    )
    control = Control()
    result = tool.run(plan, control)
    assert result.changed == 2 and result.ok
    assert [(r.placement_id, r.keep_placement_id) for r in context.requests] == [
        ("a-1-duplicate", "a-0-keep"), ("b-1-duplicate", "b-0-keep"),
    ]
    assert [event.get("current") for event in control.events] == [0, 1, 2]
    assert all(event.get("total") == 2 for event in control.events)
    assert "PRIVATE" not in str(result.summary) + str(result.warnings)


def test_replayed_views_with_identical_placements_remain_deduplicated():
    snapshot = view(A, prefix="a")
    context = Context({
        "artifact-one": snapshot,
        "artifact-two": snapshot,
        "artifact-three": snapshot,
    })
    plan = DuplicateCleanupTool().activate(context).plan(
        MaintenanceContext(scope="library", identity_id="current")
    )
    assert context.calls == ["artifact-one", "artifact-two", "artifact-three"]
    assert plan.facts["pairs"] == (("a-1-duplicate", "a-0-keep"),)
    assert plan.facts["read_failures"] == 0
    assert plan.changes == ("Supprimer le doublon n°1",)


def test_another_identity_remains_outside_its_scoped_cleanup_plan():
    context = Context({
        A: view(A, prefix="a", identity="other"),
        B: view(B, prefix="b", identity="current"),
    })
    plan = DuplicateCleanupTool().activate(context).plan(
        MaintenanceContext(scope="library", identity_id="current")
    )
    assert plan.facts["pairs"] == (("b-1-duplicate", "b-0-keep"),)
    assert plan.changes == ("Supprimer le doublon n°1",)


def test_poisoned_placement_id_does_not_discard_healthy_sibling_pair():
    class PoisonedPlacement:
        kind = "library"
        identity_id = "current"
        artifact_id = A

        @property
        def placement_id(self):
            raise RuntimeError("Bearer PRIVATE_ACCOUNT /home/PRIVATE_BOOK")

    snapshot = view(A, prefix="a")
    snapshot.placements = (
        snapshot.placements[0], PoisonedPlacement(), snapshot.placements[1],
    )
    context = Context({A: snapshot})
    plan = DuplicateCleanupTool().activate(context).plan(
        MaintenanceContext(scope="library", identity_id="current")
    )
    assert plan.facts["pairs"] == (("a-1-duplicate", "a-0-keep"),)
    assert plan.facts["read_failures"] == 1
    assert len(plan.changes) == 1
    exposed = plan.summary + " ".join(plan.warnings + plan.changes)
    for secret in ("PRIVATE", "Bearer", "/home/"):
        assert secret not in exposed
