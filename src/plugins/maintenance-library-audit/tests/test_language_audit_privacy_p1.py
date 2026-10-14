"""#1494: the read-only language audit must not export raw catalogue facts.

No network, GTK or file mutations. The precomputed plan cannot report an
invented progress ratio to its maintenance consumer.
"""
from __future__ import annotations

import json

from cifereca.plugin_api import MaintenanceContext

from cifereca_library_audits import LanguageAuditTool


class Catalogue:
    def catalog_entries(self, **options):
        assert options["identity_id"] == "current"
        return [
            {"title": "PRIVATE_BOOK_TITLE", "metadata": {
                "languages": [
                    "FR",
                    "garbage/PRIVATE_TOKEN?apikey=PRIVATE_SECRET",
                    "en",
                ],
            }},
            {"name": "PRIVATE_READER_NAME", "languages": [
                "EN", "PRIVATE_CREDENTIAL", "fr",
            ]},
            {"id": "PRIVATE_REMOTE_ID", "metadata": {"language": "it"}},
        ]


class Context:
    def broker(self, name):
        assert name == "catalog"
        return Catalogue()


class Control:
    def __init__(self):
        self.events = []
        self.checks = 0

    def check_cancelled(self):
        self.checks += 1

    def report(self, **event):
        self.events.append(event)


def test_language_preview_keeps_diagnostic_counts_without_private_raw_values():
    tool = LanguageAuditTool().activate(Context())
    plan = tool.plan(MaintenanceContext(scope="library", identity_id="current"))

    assert plan.facts["language_values"] == 7
    assert plan.facts["noncanonical"] >= 2
    assert plan.facts["invalid"] >= 2
    assert any("Langue invalide" in warning for warning in plan.warnings)
    assert any("Langue non canonique" in warning for warning in plan.warnings)
    assert all("notice n°" in warning for warning in plan.warnings)
    assert all("PRIVATE" not in warning for warning in plan.warnings)

    control = Control()
    result = tool.run(plan, control)
    assert result.changed == 0 and result.ok
    assert control.checks == 2
    assert control.events == [{"message": plan.summary}]
    assert result.facts == dict(plan.facts)
    assert result.warnings == plan.warnings

    diagnostic = json.dumps({
        "summary": result.summary,
        "warnings": result.warnings,
        "facts": result.facts,
        "progress": control.events,
    }, ensure_ascii=False)
    for private in (
        "PRIVATE_BOOK_TITLE", "PRIVATE_READER_NAME", "PRIVATE_REMOTE_ID",
        "PRIVATE_TOKEN", "PRIVATE_SECRET", "PRIVATE_CREDENTIAL",
        "garbage/", "?apikey=", "FR", "EN",
    ):
        assert private not in diagnostic


def test_language_preview_is_bounded_even_with_malicious_repeated_tags():
    class ManyCatalogue:
        def catalog_entries(self, **options):
            return [{
                "title": "PRIVATE_TITLE",
                "languages": [f"private/PRIVATE_TOKEN_{n}" for n in range(75)],
            }]

    class ManyContext:
        def broker(self, name):
            assert name == "catalog"
            return ManyCatalogue()

    plan = LanguageAuditTool().activate(ManyContext()).plan(
        MaintenanceContext(scope="library", identity_id="current")
    )
    assert plan.facts["language_values"] == 75
    assert plan.facts["invalid"] == 75
    assert len(plan.warnings) == 12
    assert len(set(plan.warnings)) == 1
    assert all("PRIVATE" not in warning for warning in plan.warnings)
