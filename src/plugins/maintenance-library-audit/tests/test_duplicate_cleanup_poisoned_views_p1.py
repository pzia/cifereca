"""#1498: never invent a delete pair from a malformed Core artifact view.

Only a synthetic metadata and library.read broker; no destructive calls.
"""
import json
from types import SimpleNamespace as NS

import pytest

from cifereca.plugin_api import MaintenanceContext
from cifereca_library_audits import DuplicateCleanupTool


class Poison:
    def __str__(self):
        raise RuntimeError("Bearer PRIVATE_TOKEN /home/PRIVATE_PATH")

    def __getattribute__(self, name):
        if name == "book_id":
            raise RuntimeError("PRIVATE_BOOK_TOKEN")
        return object.__getattribute__(self, name)


class PoisonedArtifacts:
    def __iter__(self):
        raise RuntimeError("Bearer PRIVATE_ARTIFACT_TOKEN")


class PoisonedPlacement:
    def __getattribute__(self, name):
        if name == "kind":
            raise RuntimeError("PRIVATE_PLACEMENT_PATH")
        return object.__getattribute__(self, name)


def placement(identifier, *, artifact="sha256:good", identity="alice"):
    return NS(kind="library", placement_id=identifier,
              artifact_id=artifact, identity_id=identity)


def view(book_id, placements, artifacts=("sha256:good",)):
    return NS(book=NS(book_id=book_id),
              artifacts=tuple(NS(artifact_id=ref) for ref in artifacts),
              placements=tuple(placements))


class Read:
    def __init__(self, values):
        self.values = values
        self.calls = []

    def for_artifact(self, artifact_id):
        self.calls.append(artifact_id)
        return self.values[artifact_id]


class Context:
    def __init__(self, entries, views):
        self.entries = entries
        self.read = Read(views)

    def broker(self, name):
        if name == "metadata":
            return NS(library_entries=lambda **kw: list(self.entries))
        if name == "library.read":
            return self.read
        raise AssertionError(name)


def test_poisoned_views_do_not_abort_or_synthesize_deletions():
    entries = [{"artifact_id": x} for x in (
        "badbook", "badartifacts", "badplacement", "badid", "valid"
    )]
    values = {
        "badbook": NS(book=Poison(), artifacts=(), placements=()),
        "badartifacts": NS(book=NS(book_id="b2"), artifacts=PoisonedArtifacts(), placements=()),
        "badplacement": view("b3", [PoisonedPlacement()]),
        "badid": view("b4", [placement("bad\nprivate"), placement("keeper")]),
        "valid": view("b5", [placement("p2"), placement("p1")]),
    }
    ctx = Context(entries, values)
    plan = DuplicateCleanupTool().activate(ctx).plan(
        MaintenanceContext(scope="library", identity_id="alice")
    )
    assert ctx.read.calls == list(values)
    assert plan.facts == {
        "pairs": (("p2", "p1"),), "read_failures": 4,
    }
    assert plan.changes == ("Supprimer le doublon n°1",)
    assert plan.warnings == (
        "Chaque suppression est revérifiée par Core comme doublon du même artefact.",
        "4 artefact(s) non inspecté(s) : broker library.read en erreur.",
    )
    public = json.dumps({
        "summary": plan.summary, "changes": plan.changes,
        "warnings": plan.warnings, "read_failures": plan.facts["read_failures"],
    }, ensure_ascii=False)
    for secret in ("PRIVATE", "Bearer", "/home/", "badartifacts", "badplacement"):
        assert secret not in public


def test_repeated_copy_of_one_placement_does_not_create_duplicate_pair():
    single = view("b1", [placement("p1"), placement("p1")])
    plan = DuplicateCleanupTool().activate(Context(
        [{"artifact_id": "a1"}], {"a1": single},
    )).plan(MaintenanceContext(scope="library", identity_id="alice"))
    assert plan.facts == {"pairs": (), "read_failures": 0}
    assert plan.changes == ()


@pytest.mark.parametrize("invalid", (None, True, 123, "x" * 1025, "has\ncontrol", " padded "))
def test_untrusted_artifact_or_placement_ids_cannot_create_delete_pair(invalid):
    poisoned = view("b1", [placement("keeper"), placement("duplicate", artifact=invalid)])
    plan = DuplicateCleanupTool().activate(Context(
        [{"artifact_id": "one"}], {"one": poisoned},
    )).plan(MaintenanceContext(scope="library", identity_id="alice"))
    assert plan.facts == {"pairs": (), "read_failures": 1}
