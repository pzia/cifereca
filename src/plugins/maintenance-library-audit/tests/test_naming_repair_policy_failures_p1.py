"""#1497: naming repair skips only placements without a safe Core proposal.

Synthetic public brokers. All filesystem mutations remain delegated to Core.
"""
from __future__ import annotations

import json

from cifereca.plugin_api import (
    ArtifactPlacementRef, BookRef, LibraryMutationResult,
    LogicalBookArtifacts, MaintenanceContext,
)
from cifereca_library_audits import NamingRepairTool


ARTIFACT = "sha256:" + "a" * 64


class Metadata:
    def library_entries(self, **options):
        assert options == {"identity_id": "current"}
        return [{"artifact_id": ARTIFACT, "title": "PRIVATE_BOOK_TITLE"}]


class Read:
    def __init__(self, placements):
        self.placements = tuple(placements)

    def for_artifact(self, artifact_id):
        assert artifact_id == ARTIFACT
        return LogicalBookArtifacts(
            BookRef("PRIVATE_BOOK_ID", "PRIVATE_BOOK_TITLE", ("PRIVATE_AUTHOR",)),
            artifacts=(),
            placements=self.placements,
        )


class Naming:
    def __init__(self):
        self.calls = []

    def preview(self, **options):
        self.calls.append(options)
        if options["extension"] == "epub":
            PrivateFailure = type("Bearer_PRIVATE_CLASS_TOKEN", (RuntimeError,), {})
            raise PrivateFailure("/home/PRIVATE_ACCOUNT/secret.epub")
        if options["extension"] == "mobi":
            return " "
        return "safe-new.pdf"


class Maintenance:
    def __init__(self):
        self.requests = []

    def plan(self, request):
        self.requests.append(request)
        return request

    def run(self, request, control):
        return LibraryMutationResult(
            True, changed=1, summary="PRIVATE_CORE_RESPONSE",
        )


class Context:
    def __init__(self, placements):
        self.naming = Naming()
        self.maintenance = Maintenance()
        self.brokers = {
            "metadata": Metadata(),
            "library.read": Read(placements),
            "library.naming": self.naming,
            "library.maintenance": self.maintenance,
        }

    def broker(self, name):
        return self.brokers[name]


class Control:
    def __init__(self):
        self.events = []

    def check_cancelled(self):
        pass

    def report(self, **event):
        self.events.append(event)


def _placement(identifier, extension):
    return ArtifactPlacementRef(
        identifier, ARTIFACT, "current",
        filename=f"PRIVATE_SOURCE_{identifier}.{extension}",
    )


def test_failed_renaming_preview_never_blocks_an_independent_placement():
    context = Context([
        _placement("PRIVATE_EPUB", "epub"),
        _placement("PRIVATE_MOBI", "mobi"),
        _placement("PRIVATE_PDF", "pdf"),
        ArtifactPlacementRef(
            "PRIVATE_OTHER_ACCOUNT", ARTIFACT, "other",
            filename="PRIVATE_FOREIGN.epub",
        ),
    ])
    tool = NamingRepairTool().activate(context)
    plan = tool.plan(MaintenanceContext(scope="library", identity_id="current"))

    assert len(context.naming.calls) == 3
    assert plan.facts == {
        "placement_ids": ("PRIVATE_PDF",), "skipped": 0,
        "policy_failures": 2, "read_failures": 0,
    }
    assert plan.summary == "1 placement(s) à renormaliser selon la politique Core."
    assert plan.changes == ("Renommer le placement n°1",)
    assert plan.warnings == (
        "2 placement(s) sans proposition valide de la politique Core.",
    )
    exported = json.dumps({
        "summary": plan.summary, "changes": plan.changes,
        "warnings": plan.warnings,
    })
    for private in ("PRIVATE", "/home/", "Bearer", "secret.epub"):
        assert private not in exported

    control = Control()
    result = tool.run(plan, control)
    assert result.ok and result.changed == 1
    assert result.facts == {"planned": 1, "failures": 0}
    assert [req.placement_id for req in context.maintenance.requests] == [
        "PRIVATE_PDF",
    ]
    assert [(e.get("current"), e.get("total")) for e in control.events] == [
        (0, 1), (1, 1),
    ]
    assert plan.warnings == result.warnings
    assert "PRIVATE" not in str(result.warnings) + result.summary


def test_all_preview_failures_cause_no_core_mutation_or_false_progress():
    context = Context([_placement(f"PRIVATE_BAD_{i}", "epub") for i in range(35)])
    tool = NamingRepairTool().activate(context)
    plan = tool.plan(MaintenanceContext(scope="library", identity_id="current"))
    assert plan.facts == {
        "placement_ids": (), "skipped": 0,
        "policy_failures": 35, "read_failures": 0,
    }
    assert plan.changes == ()
    assert plan.warnings == (
        "35 placement(s) sans proposition valide de la politique Core.",
    )
    control = Control()
    result = tool.run(plan, control)
    assert result.ok and result.changed == 0
    assert result.facts == {"planned": 0, "failures": 0}
    assert context.maintenance.requests == []
    assert len(control.events) == 2
    assert all("current" not in e and "total" not in e for e in control.events)
    assert "PRIVATE" not in str(result.warnings) + result.summary
