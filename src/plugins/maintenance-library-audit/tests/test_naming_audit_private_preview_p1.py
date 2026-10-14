"""#1496: naming audit previews disclose counts, never private filenames.

Offline contract test, no filesystem operations and no naming policy changes.
"""
from __future__ import annotations

import json

from cifereca.plugin_api import MaintenanceContext
from cifereca_library_audits import NamingAuditTool


class Metadata:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def library_entries(self, **options):
        self.calls.append(options)
        return list(self.rows)


class Naming:
    def __init__(self):
        self.calls = []

    def preview(self, **options):
        self.calls.append(options)
        if options["title"] == "PRIVATE_MATCHED":
            return "MATCHED.epub"
        return "PRIVATE_EXPECTED_TOKEN.epub"


class Context:
    def __init__(self, rows):
        self.metadata = Metadata(rows)
        self.naming = Naming()

    def broker(self, name):
        return {"metadata": self.metadata, "library.naming": self.naming}[name]


class Control:
    def __init__(self):
        self.events = []
        self.checks = 0

    def report(self, **data):
        self.events.append(data)

    def check_cancelled(self):
        self.checks += 1


def test_naming_audit_does_not_export_private_current_or_suggested_filenames():
    context = Context([
        {"title": "PRIVATE_TITLE_1", "author": "PRIVATE_AUTHOR",
         "filename": "PRIVATE_SOURCE_TOKEN.epub", "format": "epub"},
        {"title": "PRIVATE_TITLE_2", "path": "/home/PRIVATE_USER/PRIVATE_FILE.epub",
         "format": "epub"},
        {"title": "PRIVATE_MATCHED", "filename": "MATCHED.epub"},
        {"title": "", "filename": "PRIVATE_SKIPPED.epub"},
        {"title": "PRIVATE_NAME_5", "filename": "PRIVATE_OTHER2.epub"},
    ])
    tool = NamingAuditTool().activate(context)
    plan = tool.plan(MaintenanceContext(scope="library", identity_id="current"))
    assert context.metadata.calls == [{"identity_id": "current"}]
    assert len(context.naming.calls) == 4
    assert plan.facts == {"checked": 4, "mismatch": 3, "unavailable": 1, "policy_failures": 0}
    assert plan.warnings == (
        "Nom non conforme : notice n°1",
        "Nom non conforme : notice n°2",
        "Nom non conforme : notice n°5",
        "1 entrée(s) sans nom/titre public vérifiable.",
        "Audit uniquement : aucune mutation de placement n'est effectuée.",
    )
    control = Control()
    result = tool.run(plan, control)
    assert result.ok and result.changed == 0
    assert result.warnings == plan.warnings
    assert result.facts == dict(plan.facts)
    assert control.checks == 2
    assert control.events == [{"message": plan.summary}]

    public = json.dumps({
        "summary": result.summary, "warnings": result.warnings,
        "facts": result.facts, "events": control.events,
    }, ensure_ascii=False)
    for secret in (
        "PRIVATE_", "/home/", "TOKEN", "SOURCE", "AUTHOR",
        "EXPECTED", "USER", "MATCHED.epub", "OTHER2",
    ):
        assert secret not in public


def test_naming_audit_warning_cap_keeps_full_counts():
    context = Context([
        {"title": f"PRIVATE_TITLE_{index}", "filename": f"PRIVATE_FILENAME_{index}.epub"}
        for index in range(23)
    ])
    plan = NamingAuditTool().activate(context).plan(
        MaintenanceContext(scope="library", identity_id="current")
    )
    assert plan.facts == {"checked": 23, "mismatch": 23, "unavailable": 0, "policy_failures": 0}
    assert plan.warnings[:2] == (
        "Nom non conforme : notice n°1",
        "Nom non conforme : notice n°2",
    )
    assert plan.warnings[11] == "Nom non conforme : notice n°12"
    assert len(plan.warnings) == 13  # 12 samples plus read-only footer
    assert all("PRIVATE" not in row for row in plan.warnings)
