"""#2835: maintenance never treats a reader convention as a disposable suffix."""
import pytest

from cifereca.plugin_api import LibraryMutationPlan, MaintenanceContext
from cifereca_library_audits import NamingAuditTool, NamingRepairTool
from tests.test_naming_audit_private_preview_p1 import Context as AuditContext
from tests.test_naming_repair_policy_failures_p1 import Context, Control, _placement


@pytest.mark.parametrize("extension", ["epub", "kepub.epub", "fxl.kepub.epub", "FXL.KePub.EPUB"])
def test_audit_and_repair_send_exact_extension_to_core(extension):
    audit = AuditContext([{"title": "Book", "filename": "Book." + extension}])
    audit.naming.preview = lambda **options: (
        audit.naming.calls.append(options) or "Book." + options["extension"]
    )
    context = MaintenanceContext(scope="library", identity_id="current")
    result = NamingAuditTool().activate(audit).plan(context)
    assert audit.naming.calls[0]["extension"] == extension
    assert result.facts["checked"] == 1 and result.facts["mismatch"] == 0

    repair = Context([_placement("book", extension)])
    repair.naming.preview = lambda **options: (
        repair.naming.calls.append(options) or "Renamed." + options["extension"]
    )
    plan = NamingRepairTool().activate(repair).plan(context)
    assert repair.naming.calls[0]["extension"] == extension
    assert plan.facts["placement_ids"] == ("book",)


@pytest.mark.parametrize("extension,proposed", [
    ("fxl.kepub.epub", "Book.epub"),
    ("kepub.epub", "Book.fxl.kepub.epub"),
    ("epub", "Book.kepub.epub"),
    ("FXL.KePub.EPUB", "Book.fxl.kepub.epub"),
])
def test_audit_and_repair_refuse_a_different_convention(extension, proposed):
    context = MaintenanceContext(scope="library", identity_id="current")
    audit = AuditContext([{"title": "Book", "filename": "Book." + extension}])
    audit.naming.preview = lambda **_options: proposed
    result = NamingAuditTool().activate(audit).plan(context)
    assert result.facts["checked"] == 0 and result.facts["policy_failures"] == 1

    repair = Context([_placement("book", extension)])
    repair.naming.preview = lambda **_options: proposed
    plan = NamingRepairTool().activate(repair).plan(context)
    assert plan.facts["placement_ids"] == () and plan.facts["policy_failures"] == 1


def test_old_core_lossy_plan_never_reaches_mutation_execution():
    context = Context([_placement("book", "fxl.kepub.epub")])
    context.naming.preview = lambda **options: "Renamed." + options["extension"]
    tool = NamingRepairTool().activate(context)
    plan = tool.plan(MaintenanceContext(scope="library", identity_id="current"))
    assert plan.facts["placement_ids"] == ("book",)
    def legacy_plan(request):
        return LibraryMutationPlan(
            "normalize_filename", "old Core", request,
            changes=("Book.fxl.kepub.epub", "Renamed.epub"),
            facts={"new_filename": "Renamed.epub"},
        )
    context.maintenance.plan = legacy_plan
    calls = []
    context.maintenance.run = lambda *args: calls.append(args)
    result = tool.run(plan, Control())
    assert not result.ok and result.changed == 0 and result.facts["failures"] == 1
    assert calls == []
