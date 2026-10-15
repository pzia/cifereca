"""#1494: cached language audits must not echo arbitrary provider text."""
from dataclasses import replace

import pytest

from cifereca.plugin_api import MaintenanceContext
from cifereca_library_audits import LanguageAuditTool


class Context:
    def broker(self, name):
        assert name == "catalog"
        return type("Catalogue", (), {"catalog_entries": lambda _self, **_kw: [
            {"language": "fr"},
            {"language": ["en", 42]},
            {"language": "INVALID_PRIVATE_VALUE"},
        ]})()


class Control:
    def __init__(self, fail_at=None):
        self.events = []
        self.checks = 0
        self.fail_at = fail_at

    def check_cancelled(self):
        self.checks += 1
        if self.fail_at == self.checks:
            raise RuntimeError("cancelled")

    def report(self, **data):
        self.events.append(data)


def plan():
    return LanguageAuditTool().activate(Context()).plan(
        MaintenanceContext(scope="library", identity_id="alice")
    )


def test_live_plan_preserves_counts_warnings_and_message_only_progress():
    prepared = plan()
    result = LanguageAuditTool().run(prepared, control := Control())
    assert result.ok and result.changed == 0
    assert result.summary == prepared.summary
    assert result.warnings == prepared.warnings
    assert result.facts == dict(prepared.facts)
    assert control.events == [{"message": prepared.summary}]
    assert control.checks == 2


@pytest.mark.parametrize("poison", (
    {"summary": "Bearer PRIVATE_TOKEN /home/book"},
    {"warnings": ("PRIVATE_PASSWORD",)},
    {"warnings": ("Langue non canonique : notice n°0",)},
    {"warnings": ("Langue invalide : notice n°2", "Bearer PRIVATE_TOKEN")},
    {"warnings": ("Langue invalide : notice n°1",) * 13},
    {"warnings": "PRIVATE_WARNING"},
    {"tool_id": "covers.audit"},
    {"facts": {"language_values": 1, "invalid": 2, "noncanonical": 0, "malformed": 0, "normalization_failures": 0}},
    {"facts": {"language_values": True, "invalid": 0, "noncanonical": 0, "malformed": 0, "normalization_failures": 0}},
    {"facts": {"language_values": 1, "invalid": "Bearer PRIVATE", "noncanonical": 0, "malformed": 0, "normalization_failures": 0}},
    {"facts": {"language_values": 1, "invalid": 0, "noncanonical": -1, "malformed": 0, "normalization_failures": 0}},
    {"facts": ["PRIVATE"]},
    {"facts": {"language_values": 1}},
))
def test_untrusted_restored_plans_fail_closed_without_leakage(poison):
    result = LanguageAuditTool().run(replace(plan(), **poison), control := Control())
    assert result.ok is False and result.changed == 0
    assert result.facts == {"invalid_plan": 1}
    assert control.events == [{"message": "Audit des langues impossible : plan invalide"}]
    assert control.checks == 2
    for secret in ("PRIVATE", "Bearer", "/home/"):
        assert secret not in str((result.summary, result.warnings, result.facts, control.events))


def test_final_cancellation_never_reports_success():
    prepared = plan()
    control = Control(fail_at=2)
    with pytest.raises(RuntimeError, match="cancelled"):
        LanguageAuditTool().run(prepared, control)
    assert control.events == [{"message": prepared.summary}]
