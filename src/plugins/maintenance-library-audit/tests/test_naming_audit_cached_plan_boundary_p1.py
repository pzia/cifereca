"""#1496: audit execution cannot leak an untrusted restored naming plan."""
from dataclasses import replace

import pytest

from cifereca.plugin_api import MaintenanceContext
from cifereca_library_audits import NamingAuditTool


class Context:
    def broker(self, name):
        if name == "metadata":
            return type("Metadata", (), {"library_entries": lambda _self, **_kw: [
                {"title": "Public", "filename": "bad.epub"},
                {"title": "Missing", "filename": ""},
                {"title": "PRIVATE_FAIL", "filename": "private.epub"},
            ]})()
        assert name == "library.naming"
        return type("Naming", (), {
            "preview": lambda _self, **kw: (
                (_ for _ in ()).throw(RuntimeError("Bearer PRIVATE_TOKEN"))
                if kw["title"] == "PRIVATE_FAIL" else "good.epub"
            ),
        })()


class Control:
    def __init__(self, stop_at=None):
        self.events = []
        self.checks = 0
        self.stop_at = stop_at

    def check_cancelled(self):
        self.checks += 1
        if self.checks == self.stop_at:
            raise RuntimeError("cancelled")

    def report(self, **kwargs):
        self.events.append(kwargs)


def plan():
    return NamingAuditTool().activate(Context()).plan(
        MaintenanceContext(scope="library", identity_id="alice")
    )


def test_real_naming_plan_retains_failure_status_and_safe_progress():
    original = plan()
    assert original.facts["policy_failures"] == 1
    result = NamingAuditTool().run(original, control := Control())
    assert result.ok is False and result.changed == 0
    assert result.summary == original.summary
    assert result.warnings == original.warnings
    assert result.facts == original.facts
    assert control.events == [{"message": original.summary}]
    assert control.checks == 2


@pytest.mark.parametrize("poison", (
    {"summary": "Bearer PRIVATE_TOKEN /home/PRIVATE_BOOK"},
    {"warnings": ("Bearer PRIVATE_TOKEN",)},
    {"warnings": ("Nom non conforme : notice n°0",)},
    {"warnings": ("Nom non conforme : notice n°1",) * 16},
    {"warnings": ("Nom non conforme : notice n°1",)},
    {"warnings": "PRIVATE_PLAN_STRING"},
    {"tool_id": "languages.audit"},
    {"facts": {"checked": 1, "mismatch": 2, "unavailable": 0, "policy_failures": 0}},
    {"facts": {"checked": 1, "mismatch": 0, "unavailable": 0, "policy_failures": "Bearer PRIVATE_TOKEN"}},
    {"facts": {"checked": 1, "mismatch": 0, "unavailable": -1, "policy_failures": 0}},
    {"facts": {"checked": 1, "mismatch": 0, "unavailable": 0, "policy_failures": True}},
    {"facts": {"checked": 1}},
    {"facts": ["Bearer PRIVATE_TOKEN"]},
))
def test_forged_cached_naming_preview_is_rejected_without_echo(poison):
    result = NamingAuditTool().run(replace(plan(), **poison), control := Control())
    assert result.ok is False and result.changed == 0
    assert result.facts == {"invalid_plan": 1}
    assert control.events == [{"message": "Audit des noms impossible : plan invalide"}]
    assert control.checks == 2
    for secret in ("PRIVATE", "Bearer", "/home/"):
        assert secret not in str((result.summary, result.warnings, result.facts, control.events))


def test_final_cancellation_prevents_success_status():
    original = plan()
    control = Control(stop_at=2)
    with pytest.raises(RuntimeError, match="cancelled"):
        NamingAuditTool().run(original, control)
    assert control.events == [{"message": original.summary}]


def test_valid_all_clear_naming_plan_returns_success():
    original = plan()
    clean = replace(
        original,
        summary="0 nom(s) différent(s) de la politique Core sur 1 fichier(s) vérifiable(s).",
        warnings=("Audit uniquement : aucune mutation de placement n'est effectuée.",),
        facts={"checked": 1, "mismatch": 0, "unavailable": 0, "policy_failures": 0},
    )
    result = NamingAuditTool().run(clean, Control())
    assert result.ok is True
    assert result.facts["checked"] == 1
