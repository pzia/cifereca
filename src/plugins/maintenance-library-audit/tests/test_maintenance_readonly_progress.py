"""Progress producer contract for the three precomputed maintenance audits."""
import pytest

from cifereca.plugin_api import MaintenanceContext
from cifereca_library_audits import CoverAuditTool, LanguageAuditTool, NamingAuditTool


class Catalog:
    def catalog_entries(self, **options):
        return [
            {"title": "A", "metadata": {"language": "fr"}},
            {"title": "B", "thumbnail_url": "https://example.test/b.jpg"},
        ]


class Metadata:
    def library_entries(self, **options):
        return [{"title": "Livre", "filename": "old.epub", "author": "A"}]


class Naming:
    def preview(self, **options):
        return "Livre - A.epub"


class Context:
    def __init__(self, empty=False):
        self.empty = empty

    def broker(self, name):
        if name == "catalog":
            return EmptyCatalog() if self.empty else Catalog()
        if name == "metadata":
            return EmptyMetadata() if self.empty else Metadata()
        if name == "library.naming":
            return Naming()
        raise AssertionError(name)


class EmptyCatalog:
    def catalog_entries(self, **options):
        return []


class EmptyMetadata:
    def library_entries(self, **options):
        return []


class Control:
    def __init__(self):
        self.events = []
        self.checks = 0

    def check_cancelled(self):
        self.checks += 1

    def report(self, **data):
        self.events.append(data)


@pytest.mark.parametrize("tool_type", (CoverAuditTool, LanguageAuditTool, NamingAuditTool))
@pytest.mark.parametrize("empty", (False, True))
def test_precomputed_audit_never_invents_progress(tool_type, empty):
    tool = tool_type().activate(Context(empty=empty))
    plan = tool.plan(MaintenanceContext(scope="library", identity_id="id1"))
    control = Control()
    result = tool.run(plan, control)
    assert result.ok is True and result.changed == 0
    assert result.summary == plan.summary
    assert result.facts == dict(plan.facts)
    assert control.events == [{"message": plan.summary}]
    assert control.checks == 2
    assert all("current" not in e and "total" not in e for e in control.events)


class Cancelled(Exception):
    pass


@pytest.mark.parametrize("tool_type", (CoverAuditTool, LanguageAuditTool, NamingAuditTool))
def test_precomputed_audit_observes_cancellation_after_report(tool_type):
    tool = tool_type().activate(Context())
    plan = tool.plan(MaintenanceContext(scope="library", identity_id="id1"))

    class CancelAtFinalGate(Control):
        def check_cancelled(self):
            super().check_cancelled()
            if self.checks == 2:
                raise Cancelled()

    control = CancelAtFinalGate()
    with pytest.raises(Cancelled):
        tool.run(plan, control)
    assert control.events == [{"message": plan.summary}]
