"""#1495: provider metadata errors are quarantined during integrity planning.

No file reads or mutations: only synthetic Core metadata/library.read brokers.
"""
import json
from types import SimpleNamespace as NS

from cifereca.plugin_api import MaintenanceContext
from cifereca_library_audits import IntegrityAuditTool


class Poison:
    def __str__(self):
        raise RuntimeError("Bearer PRIVATE_TOKEN /home/PRIVATE_BOOK.pdf")

    def __bool__(self):
        raise RuntimeError("PRIVATE_ACCESS_TOKEN")


class BadLocal(dict):
    def get(self, key, default=None):
        if key == "local":
            raise RuntimeError("PRIVATE_ACCOUNT_PATH")
        return super().get(key, default)


class BadSize:
    @property
    def size_bytes(self):
        raise RuntimeError("Bearer PRIVATE_SIZE_TOKEN")

    format = "pdf"
    sha256 = ""


class Context:
    def __init__(self, rows, artifacts):
        self.rows = rows
        self.artifacts = artifacts
        self.read_ids = []

    def broker(self, name):
        if name == "metadata":
            return NS(library_entries=lambda **kw: list(self.rows))
        if name == "library.read":
            def artifact(artifact_id):
                self.read_ids.append(artifact_id)
                return self.artifacts[artifact_id]
            return NS(artifact=artifact)
        raise AssertionError(name)


def test_corrupt_references_preserve_valid_candidates_and_redacted_failures():
    rows = [
        BadLocal(local=True, local_path="/private/one.pdf", artifact_id="badlocal"),
        {"local": True, "local_path": Poison(), "artifact_id": "badpath"},
        {"local": True, "local_path": "/private/two.pdf", "artifact_id": Poison()},
        {"local": True, "local_path": "/private/size.pdf", "artifact_id": "badsize", "media_format": "pdf"},
        {"local": True, "local_path": "/private/format.pdf", "artifact_id": "badformat"},
        {"local": True, "local_path": "/private/sha.pdf", "artifact_id": "badsha", "media_format": "pdf"},
        {"local": True, "local_path": "/private/good.pdf", "artifact_id": "good", "media_format": "pdf"},
    ]
    artifacts = {
        "badsize": BadSize(),
        "badformat": NS(size_bytes=1, format=Poison(), sha256=""),
        "badsha": NS(size_bytes=1, format="pdf", sha256=Poison()),
        "good": NS(size_bytes=1, format="pdf", sha256=""),
    }
    ctx = Context(rows, artifacts)
    plan = IntegrityAuditTool().activate(ctx).plan(
        MaintenanceContext(scope="library", identity_id="alice")
    )
    assert ctx.read_ids == ["badsize", "badformat", "badsha", "good"]
    assert plan.facts["read_failures"] == 0
    assert plan.facts["reference_failures"] == 6
    assert plan.facts["missing_path"] == 0
    assert len(plan.facts["candidates"]) == 2
    assert plan.facts["candidates"][0]["expected_size"] == 0
    assert plan.facts["candidates"][1]["expected_size"] == 1
    assert all("title" not in row for row in plan.facts["candidates"])
    assert plan.warnings == (
        "6 référence(s) d’intégrité invalide(s) : certains contrôles indisponibles.",
    )
    exported = json.dumps({
        "summary": plan.summary, "warnings": plan.warnings,
        "counts": {k: v for k, v in plan.facts.items() if k != "candidates"},
    }, ensure_ascii=False)
    for secret in ("PRIVATE", "Bearer", "/home/", "/private/", "Poison", "RuntimeError"):
        assert secret not in exported


def test_empty_path_remains_a_missing_path_not_a_reference_failure():
    ctx = Context([{"local": True, "local_path": "", "artifact_id": "good"}], {})
    plan = IntegrityAuditTool().activate(ctx).plan(
        MaintenanceContext(scope="library", identity_id="alice")
    )
    assert plan.facts["reference_failures"] == 0
    assert plan.facts["missing_path"] == 1
    assert ctx.read_ids == []
