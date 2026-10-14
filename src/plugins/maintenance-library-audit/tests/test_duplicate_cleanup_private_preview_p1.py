"""#1498: a destructive duplicate cleanup plan must have a private execution scope
and a public, non-identifying preview. Synthetic brokers only.
"""
from __future__ import annotations

import json

from cifereca.plugin_api import (
    ArtifactFormat,
    ArtifactPlacementRef,
    ArtifactRef,
    BookRef,
    LibraryMutationResult,
    LogicalBookArtifacts,
    MaintenanceContext,
)
from cifereca_library_audits import DuplicateCleanupTool


ARTIFACT_ID = "sha256:" + "a" * 64


class Metadata:
    def library_entries(self, **options):
        assert options.get("identity_id") == "current"
        return [{"artifact_id": ARTIFACT_ID, "title": "PRIVATE_BOOK_TITLE"}]


class LibraryRead:
    def __init__(self, duplicates):
        self.duplicates = duplicates

    def for_artifact(self, artifact_id):
        assert artifact_id == ARTIFACT_ID
        placements = [ArtifactPlacementRef(
            "a-PRIVATE_KEEP_ID", artifact_id, "current",
            filename="PRIVATE_KEEP_FILENAME.epub",
        )]
        placements.extend(
            ArtifactPlacementRef(
                f"z-PRIVATE_DUPLICATE_{n:03d}", artifact_id, "current",
                filename=f"PRIVATE_DUPLICATE_FILENAME_{n}.epub",
            )
            for n in range(1, self.duplicates + 1)
        )
        # A placement from another identity must never be proposed for deletion.
        placements.append(ArtifactPlacementRef(
            "z-PRIVATE_FOREIGN", artifact_id, "other",
            filename="PRIVATE_FOREIGN_FILENAME.epub",
        ))
        return LogicalBookArtifacts(
            BookRef("PRIVATE_BOOK_ID", "PRIVATE_BOOK_TITLE", ("PRIVATE_AUTHOR",)),
            artifacts=(ArtifactRef(
                artifact_id, "PRIVATE_BOOK_ID",
                ArtifactFormat("application/epub+zip", "epub"),
                5, "a" * 64,
            ),),
            placements=tuple(placements),
        )


class Maintenance:
    def __init__(self):
        self.requests = []

    def plan(self, request):
        self.requests.append(request)
        return request

    def run(self, request, control):
        return LibraryMutationResult(True, changed=1, summary="PRIVATE_CORE_MESSAGE")


class Context:
    def __init__(self, duplicate_count):
        self.core = Maintenance()
        self.providers = {
            "metadata": Metadata(),
            "library.read": LibraryRead(duplicate_count),
            "library.maintenance": self.core,
        }

    def broker(self, key):
        return self.providers[key]


class Control:
    def __init__(self):
        self.events = []

    def check_cancelled(self):
        pass

    def report(self, **event):
        self.events.append(event)


def test_duplicate_cleanup_preview_redacts_private_names_without_changing_deletion_scope():
    context = Context(duplicate_count=2)
    tool = DuplicateCleanupTool().activate(context)
    plan = tool.plan(MaintenanceContext(scope="library", identity_id="current"))

    assert plan.facts["pairs"] == (
        ("z-PRIVATE_DUPLICATE_001", "a-PRIVATE_KEEP_ID"),
        ("z-PRIVATE_DUPLICATE_002", "a-PRIVATE_KEEP_ID"),
    )
    assert plan.changes == (
        "Supprimer le doublon n°1",
        "Supprimer le doublon n°2",
    )
    assert plan.facts["read_failures"] == 0
    assert "2 placement(s)" in plan.summary

    exported = json.dumps({
        "summary": plan.summary,
        "changes": plan.changes,
        "warnings": plan.warnings,
    }, ensure_ascii=False)
    for secret in (
        "PRIVATE", "sha256:", "a" * 24, ".epub",
        "KEEP_ID", "DUPLICATE_FILENAME",
    ):
        assert secret not in exported

    control = Control()
    result = tool.run(plan, control)
    assert result.ok and result.changed == 2
    assert result.facts == {"planned": 2, "failures": 0}
    assert [(e.get("current"), e.get("total"))
            for e in control.events] == [(0, 2), (1, 2), (2, 2)]
    assert [request.operation for request in context.core.requests] == [
        "delete_duplicate", "delete_duplicate",
    ]
    assert [request.placement_id for request in context.core.requests] == [
        "z-PRIVATE_DUPLICATE_001", "z-PRIVATE_DUPLICATE_002",
    ]
    assert all(request.keep_placement_id == "a-PRIVATE_KEEP_ID"
               for request in context.core.requests)


def test_duplicate_cleanup_preview_cap_preserves_full_private_execution_plan():
    context = Context(duplicate_count=104)
    tool = DuplicateCleanupTool().activate(context)
    plan = tool.plan(MaintenanceContext(scope="library", identity_id="current"))
    assert len(plan.facts["pairs"]) == 104
    assert len(plan.changes) == 100
    assert plan.changes[0] == "Supprimer le doublon n°1"
    assert plan.changes[-1] == "Supprimer le doublon n°100"
    assert all("PRIVATE" not in text for text in plan.changes)
