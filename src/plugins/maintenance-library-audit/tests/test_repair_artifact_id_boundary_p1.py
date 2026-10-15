"""#1497/#1498: untrusted artifact coordinates never abort a repair preview."""
from cifereca.plugin_api import (
    ArtifactPlacementRef, BookRef, LogicalBookArtifacts, MaintenanceContext,
)
from cifereca_library_audits import DuplicateCleanupTool, NamingRepairTool, _artifact_id


class Poison:
    def __str__(self):
        raise RuntimeError("Bearer PRIVATE_TOKEN /home/PRIVATE_USER")

    def __bool__(self):
        raise RuntimeError("Bearer PRIVATE_BOOLEAN")


class PoisonedMapping(dict):
    def get(self, name, default=None):
        raise RuntimeError("PRIVATE_MAPPING /home/PRIVATE_USER")


GOOD = "sha256:" + "a" * 64
BAD = "sha256:" + "b" * 64


class Metadata:
    def library_entries(self, **_kwargs):
        return [
            {"artifact_id": Poison(), "title": "PRIVATE_BAD"},
            {"artifact_id": True, "title": "PRIVATE_BOOL"},
            {"artifact_id": 123, "title": "PRIVATE_NUMBER"},
            {"artifact_id": "PRIVATE\nCONTROL", "title": "PRIVATE_BAD"},
            {"artifact_id": " " + GOOD, "title": "PRIVATE_BAD"},
            {"artifact_id": "x" * 1025, "title": "PRIVATE_BAD"},
            PoisonedMapping(title="PRIVATE_BAD"),
            {"artifact_id": BAD, "title": "PRIVATE_READ_ERROR"},
            {"artifact_id": GOOD, "title": "Title", "author": "Author"},
            {"artifact_id": GOOD, "title": "DUPLICATE_ROW"},
        ]


class LibraryRead:
    def __init__(self):
        self.calls = []

    def for_artifact(self, artifact_id):
        self.calls.append(artifact_id)
        if artifact_id == BAD:
            SecretException = type("Bearer_PRIVATE_CREDENTIAL_ERROR", (RuntimeError,), {})
            raise SecretException("PRIVATE_TOKEN /home/PRIVATE_USER")
        return LogicalBookArtifacts(
            BookRef("book-1", "Title", ("Author",)),
            artifacts=(),
            placements=(
                ArtifactPlacementRef("p1", artifact_id, "alice", filename="wrong.epub"),
                ArtifactPlacementRef("p2", artifact_id, "alice", filename="copy.epub"),
            ),
        )


class Naming:
    def preview(self, **kwargs):
        return "good.epub"


class Context:
    def __init__(self):
        self.read = LibraryRead()
        self.metadata = Metadata()

    def broker(self, name):
        return {
            "metadata": self.metadata, "library.read": self.read,
            "library.naming": Naming(),
        }[name]


def test_artifact_id_does_not_stringify_untrusted_values():
    assert _artifact_id({"artifact_id": Poison()}) == ""
    assert _artifact_id({"artifact_id": True}) == ""
    assert _artifact_id({"artifact_id": 42}) == ""
    assert _artifact_id({"artifact_id": "x" * 1025}) == ""
    assert _artifact_id({"artifact_id": "private\nkey"}) == ""
    assert _artifact_id(PoisonedMapping()) == ""
    assert _artifact_id({"artifact_id": "", "artifact": GOOD}) == GOOD
    assert _artifact_id({"artifact_id": GOOD}) == GOOD


def test_naming_repair_skips_invalid_refs_and_keeps_valid_placements():
    context = Context()
    plan = NamingRepairTool().activate(context).plan(
        MaintenanceContext(scope="library", identity_id="alice")
    )
    assert context.read.calls == [BAD, GOOD]
    assert plan.facts["placement_ids"] == ("p1", "p2")
    assert plan.facts["read_failures"] == 1
    assert plan.changes == (
        "Renommer le placement n°1",
        "Renommer le placement n°2",
    )
    assert all("PRIVATE" not in warning and "Bearer" not in warning
               for warning in plan.warnings)
    assert "library.read en erreur" in " ".join(plan.warnings)


def test_duplicate_cleanup_skips_invalid_refs_and_keeps_safe_pairs():
    context = Context()
    plan = DuplicateCleanupTool().activate(context).plan(
        MaintenanceContext(scope="library", identity_id="alice")
    )
    assert context.read.calls == [BAD, GOOD]
    assert plan.facts["pairs"] == (("p2", "p1"),)
    assert plan.facts["read_failures"] == 1
    assert plan.changes == ("Supprimer le doublon n°1",)
    assert all("PRIVATE" not in warning and "Bearer" not in warning
               for warning in plan.warnings)
