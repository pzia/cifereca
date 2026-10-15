"""#1494: language audit quarantines malformed provider rows, not the whole scan."""
import json

import pytest

from cifereca.plugin_api import MaintenanceContext
from cifereca_library_audits import LanguageAuditTool


class PoisonedMapping(dict):
    def get(self, key, default=None):
        raise RuntimeError("Bearer PRIVATE_TOKEN /home/PRIVATE_LIBRARY")


class PoisonedList(list):
    def __iter__(self):
        raise RuntimeError("Bearer PRIVATE_ACCESS_TOKEN")


class Context:
    def __init__(self, rows):
        self.rows = rows

    def broker(self, name):
        assert name == "catalog"
        return type("Catalog", (), {
            "catalog_entries": lambda _self, **_kw: self.rows,
        })()


class Control:
    def __init__(self):
        self.events = []

    def check_cancelled(self):
        return None

    def report(self, **kwargs):
        self.events.append(kwargs)


@pytest.mark.parametrize("poison", (
    PoisonedMapping(language="PRIVATE"),
    {"metadata": PoisonedMapping(language="PRIVATE")},
    {"languages": PoisonedList(["PRIVATE"])},
    {"metadata": {"languages": PoisonedList(["PRIVATE"])}},
))
def test_broken_provider_language_row_keeps_next_notice_and_redacts_diagnostics(poison):
    context = Context([
        {"language": "fr"},
        poison,
        {"languages": ["en"]},
    ])
    tool = LanguageAuditTool().activate(context)
    plan = tool.plan(MaintenanceContext(scope="library", identity_id="alice"))
    assert plan.facts == {
        "language_values": 2, "invalid": 0, "noncanonical": 0,
        "malformed": 1, "normalization_failures": 0,
    }
    assert plan.warnings == ("Langue non textuelle : notice n°2",)
    control = Control()
    result = tool.run(plan, control)
    assert result.ok is True and result.changed == 0
    assert control.events == [{"message": plan.summary}]
    exported = json.dumps({
        "summary": result.summary, "warnings": result.warnings,
        "facts": result.facts, "progress": control.events,
    }, ensure_ascii=False)
    for secret in ("PRIVATE", "Bearer", "/home/", "RuntimeError"):
        assert secret not in exported
