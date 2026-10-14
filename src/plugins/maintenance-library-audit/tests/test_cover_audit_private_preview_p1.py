"""#1493: read-only cover audits never expose private preview data.

Synthetic catalog only: no network, file reads, downloads or metadata writes.
"""
from __future__ import annotations

import json

from cifereca.plugin_api import MaintenanceContext
from cifereca_library_audits import CoverAuditTool


class Catalog:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def catalog_entries(self, **options):
        self.calls.append(options)
        return list(self.rows)


class Context:
    def __init__(self, catalog):
        self.catalog = catalog

    def broker(self, name):
        assert name == "catalog"
        return self.catalog


class Control:
    def __init__(self):
        self.events = []
        self.checks = 0

    def report(self, **event):
        self.events.append(event)

    def check_cancelled(self):
        self.checks += 1


def test_cover_preview_redacts_titles_urls_and_handles_malformed_urls():
    catalog = Catalog([
        {"title": "PRIVATE_BOOK_TITLE", "id": "PRIVATE_ID", "metadata": {}},
        {"name": "PRIVATE_READER_NAME", "cover_url": "javascript:PRIVATE_TOKEN"},
        {"title": "PRIVATE_MALFORMED_TITLE",
         "thumbnail_url": "https://[PRIVATE_UNBALANCED/cover.jpg"},
        {"title": "Valid cover", "thumbnail_url": "https://example.test/cover.jpg"},
        {"title": "Local cover", "metadata": {"cover": "file:///some/local/PRIVATE_SECRET.jpg"}},
    ])
    tool = CoverAuditTool().activate(Context(catalog))
    plan = tool.plan(MaintenanceContext(scope="library", identity_id="current"))
    assert catalog.calls == [{"identity_id": "current"}]
    assert plan.facts == {"entries": 5, "missing": 1, "invalid": 2}
    assert plan.warnings == (
        "Sans couverture : notice n°1",
        "Couverture non reconnue : notice n°2",
        "Couverture non reconnue : notice n°3",
    )
    assert tool.describe().danger_level == "read-only"

    control = Control()
    result = tool.run(plan, control)
    assert result.changed == 0 and result.ok
    assert result.facts == dict(plan.facts)
    assert result.warnings == plan.warnings
    assert control.checks == 2
    assert control.events == [{"message": plan.summary}]

    exported = json.dumps({
        "summary": result.summary, "warnings": result.warnings,
        "facts": result.facts, "events": control.events,
    }, ensure_ascii=False)
    for secret in (
        "PRIVATE", "javascript:", "file://", "http", "cover.jpg",
        "BOOK_TITLE", "READER_NAME", "SECRET",
    ):
        assert secret not in exported


def test_cover_preview_warning_cap_and_independent_scanning():
    rows = [
        {"id": f"PRIVATE_ID_{index}", "title": "PRIVATE_TITLE"}
        for index in range(20)
    ]
    rows += [{"title": "valid", "thumbnail_url": "https://example.test/c.png"}]
    catalog = Catalog(rows)
    plan = CoverAuditTool().activate(Context(catalog)).plan(
        MaintenanceContext(scope="library", identity_id="current")
    )
    assert plan.facts == {"entries": 21, "missing": 20, "invalid": 0}
    assert len(plan.warnings) == 12
    assert plan.warnings[0] == "Sans couverture : notice n°1"
    assert plan.warnings[-1] == "Sans couverture : notice n°12"
    assert "PRIVATE" not in json.dumps(plan.warnings)
