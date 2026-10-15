"""#1496: a poisoned bibliographic item cannot abort a read-only naming scan."""
import json

import pytest

from cifereca.plugin_api import MaintenanceContext
from cifereca_library_audits import NamingAuditTool


class SecretBomb:
    def __str__(self):
        raise RuntimeError("Bearer PRIVATE_ACCOUNT /home/PRIVATE_USER/file.epub")


class Naming:
    def __init__(self):
        self.calls = []

    def preview(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs["title"] == "broken-policy-output":
            return SecretBomb()
        return "correct.epub"


class Context:
    def __init__(self, rows):
        self.rows = rows
        self.naming = Naming()

    def broker(self, name):
        return (
            type("Metadata", (), {
                "library_entries": lambda _self, **_kw: list(self.rows),
            })()
            if name == "metadata" else self.naming
        )


class Control:
    def __init__(self):
        self.events = []

    def check_cancelled(self):
        pass

    def report(self, **kwargs):
        self.events.append(kwargs)


@pytest.mark.parametrize("poisoned", (
    {"title": SecretBomb(), "filename": "present.epub"},
    {"title": "bad-filename", "filename": SecretBomb()},
    {"title": "bad-path", "path": SecretBomb()},
    {"title": "bad-format", "filename": "notext", "format": SecretBomb()},
    {"title": "bad-author", "filename": "present.epub", "author": SecretBomb()},
    {"title": "bad-series", "filename": "present.epub", "series": SecretBomb()},
    {"title": "bad-index", "filename": "present.epub", "series_index": SecretBomb()},
    {"title": "broken-policy-output", "filename": "present.epub"},
))
def test_untrusted_metadata_and_proposed_names_are_quarantined_per_row(poisoned):
    ctx = Context([
        {"title": "First", "filename": "correct.epub"},
        poisoned,
        {"title": "Third", "filename": "wrong.epub"},
    ])
    tool = NamingAuditTool().activate(ctx)
    plan = tool.plan(MaintenanceContext(scope="library", identity_id="alice"))
    assert plan.facts == {
        "checked": 2, "mismatch": 1, "unavailable": 0, "policy_failures": 1,
    }
    assert len(ctx.naming.calls) in (2, 3)
    assert plan.warnings == (
        "Politique de nommage indisponible : notice n°2",
        "Nom non conforme : notice n°3",
        "1 entrée(s) sans proposition valide de la politique Core.",
        "Audit uniquement : aucune mutation de placement n'est effectuée.",
    )
    control = Control()
    result = tool.run(plan, control)
    assert result.ok is False and result.changed == 0
    assert control.events == [{"message": plan.summary}]
    public = json.dumps({
        "summary": result.summary, "warnings": result.warnings,
        "facts": result.facts, "progress": control.events,
    }, ensure_ascii=False)
    for secret in ("PRIVATE", "Bearer", "/home/", "RuntimeError", "SecretBomb"):
        assert secret not in public


def test_empty_bibliographic_fields_still_count_as_unavailable_not_policy_error():
    ctx = Context([
        {"title": "", "filename": "file.epub"},
        {"title": "No path", "filename": None},
        {"title": "Usable", "filename": "correct.epub"},
    ])
    plan = NamingAuditTool().activate(ctx).plan(MaintenanceContext(scope="library"))
    assert plan.facts == {
        "checked": 1, "mismatch": 0, "unavailable": 2, "policy_failures": 0,
    }
    assert ctx.naming.calls[0]["title"] == "Usable"
