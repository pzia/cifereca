"""#1496: one failing Core naming-policy preview cannot abort a catalogue audit.

Synthetic brokers only. No local filesystem mutation, network, or GTK.
"""
from __future__ import annotations

import json

import pytest

from cifereca.plugin_api import MaintenanceContext
from cifereca_library_audits import NamingAuditTool


class Metadata:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def library_entries(self, **options):
        self.calls.append(options)
        return self.rows


class Naming:
    def __init__(self):
        self.calls = []

    def preview(self, **options):
        self.calls.append(options)
        title = options["title"]
        if title == "PRIVATE_FAILING_TITLE":
            class Bearer_PRIVATE_TOKEN_Exception(RuntimeError):
                pass
            raise Bearer_PRIVATE_TOKEN_Exception(
                "/home/PRIVATE_ACCOUNT/book.epub?token=PRIVATE_SECRET"
            )
        if title == "PRIVATE_NONE":
            return None
        if title == "PRIVATE_EMPTY":
            return "   "
        if title == "PRIVATE_MATCHED":
            return "matched.epub"
        return "proposed.epub"


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

    def check_cancelled(self):
        self.checks += 1

    def report(self, **kwargs):
        self.events.append(kwargs)


def test_naming_audit_continues_after_broker_errors_and_empty_previews():
    context = Context([
        {"title": "PRIVATE_MATCHED", "filename": "matched.epub"},
        {"title": "PRIVATE_FAILING_TITLE", "filename": "secret.epub"},
        {"title": "PRIVATE_SECOND", "filename": "wrong.epub"},
        {"title": "PRIVATE_NONE", "filename": "secret2.epub"},
        {"title": "PRIVATE_EMPTY", "filename": "secret3.epub"},
        {"title": "", "filename": "PRIVATE_MISSING.epub"},
        {"title": "PRIVATE_THIRD", "filename": "proposed.epub"},
    ])
    tool = NamingAuditTool().activate(context)
    plan = tool.plan(MaintenanceContext(scope="library", identity_id="current"))
    assert context.metadata.calls == [{"identity_id": "current"}]
    assert len(context.naming.calls) == 6
    assert plan.facts == {
        "checked": 3, "mismatch": 1, "unavailable": 1, "policy_failures": 3,
    }
    assert plan.summary == "1 nom(s) différent(s) de la politique Core sur 3 fichier(s) vérifiable(s)."
    assert plan.warnings == (
        "Politique de nommage indisponible : notice n°2",
        "Nom non conforme : notice n°3",
        "Politique de nommage indisponible : notice n°4",
        "Politique de nommage indisponible : notice n°5",
        "1 entrée(s) sans nom/titre public vérifiable.",
        "3 entrée(s) sans proposition valide de la politique Core.",
        "Audit uniquement : aucune mutation de placement n'est effectuée.",
    )
    control = Control()
    result = tool.run(plan, control)
    assert not result.ok and result.changed == 0
    assert result.warnings == plan.warnings
    assert result.facts == dict(plan.facts)
    assert control.events == [{"message": plan.summary}]
    assert control.checks == 2

    public = json.dumps({
        "summary": result.summary, "warnings": result.warnings,
        "facts": result.facts, "progress": control.events,
    }, ensure_ascii=False)
    for private in (
        "PRIVATE", "Bearer", "/home/", "secret", "Exception",
        "PASSWORD", "secret.epub",
    ):
        assert private not in public


def test_every_naming_policy_error_is_counted_but_sampled_to_twelve():
    rows = [
        {"title": "PRIVATE_FAILING_TITLE", "filename": f"PRIVATE_FILE_{i}.epub"}
        for i in range(50)
    ]
    context = Context(rows)
    plan = NamingAuditTool().activate(context).plan(
        MaintenanceContext(scope="library", identity_id="current")
    )
    assert plan.facts == {
        "checked": 0, "mismatch": 0, "unavailable": 0, "policy_failures": 50,
    }
    assert len(context.naming.calls) == 50
    assert len(plan.warnings) == 14  # 12 safe ordinals, aggregate, footer
    assert plan.warnings[0] == "Politique de nommage indisponible : notice n°1"
    assert plan.warnings[11] == "Politique de nommage indisponible : notice n°12"
    assert plan.warnings[12] == "50 entrée(s) sans proposition valide de la politique Core."
    assert all("PRIVATE" not in warning for warning in plan.warnings)
