"""#1495: invalid metadata must not be sent to the integrity Core broker."""
from cifereca.plugin_api import MaintenanceContext
from cifereca_library_audits import IntegrityAuditTool


class Poison:
    def __bool__(self):
        raise RuntimeError("PRIVATE_TOKEN")

    def __str__(self):
        raise RuntimeError("PRIVATE_PATH")


class BrokenRow(dict):
    def get(self, key, default=None):
        raise RuntimeError("Bearer PRIVATE_TOKEN")


class Read:
    def __init__(self):
        self.calls = []

    def artifact(self, identifier):
        self.calls.append(identifier)
        return None


class Context:
    def __init__(self, rows):
        self.rows = rows
        self.read = Read()

    def broker(self, name):
        if name == "metadata":
            return type("Metadata", (), {"library_entries": lambda _self, **_kw: self.rows})()
        assert name == "library.read"
        return self.read


def test_integrity_plan_quarantines_invalid_rows_without_aborting():
    context = Context([
        BrokenRow(local=True),
        {"local": True, "local_path": Poison()},
        {"local": True, "local_path": "/tmp/good.pdf", "artifact_id": Poison()},
        {"local": True, "local_path": "/tmp/next.pdf", "artifact_id": "artifact-ok"},
    ])
    plan = IntegrityAuditTool().activate(context).plan(
        MaintenanceContext(scope="library", identity_id="alice")
    )
    assert context.read.calls == ["artifact-ok"]
    assert [c["path"] for c in plan.facts["candidates"]] == [
        "/tmp/next.pdf",
    ]
    assert plan.facts["reference_failures"] == 3
    assert plan.facts["missing_path"] == 0
    assert "PRIVATE" not in str(plan.warnings)
    assert "Bearer" not in str(plan.warnings)
