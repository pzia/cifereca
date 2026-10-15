"""#1495: restored integrity plans never export arbitrary private warnings.

No real files, network, Gtk or Core brokers are involved in these tests.
"""
import json

import pytest

from cifereca.plugin_api import MaintenancePlan
from cifereca_library_audits import IntegrityAuditTool


class Control:
    def __init__(self):
        self.events = []
        self.checks = 0

    def check_cancelled(self):
        self.checks += 1

    def report(self, **event):
        self.events.append(event)


def run_plan(facts, warnings=("Bearer PRIVATE_ACCESS_TOKEN",), *, tool_id=None):
    tool = IntegrityAuditTool()
    control = Control()
    plan = MaintenancePlan(
        tool_id=tool.info.id if tool_id is None else tool_id,
        summary="PRIVATE_BOOK /home/PRIVATE_USER/books/book.epub",
        warnings=warnings,
        facts=facts,
    )
    result = tool.run(plan, control)
    return result, control


def test_restored_plan_rebuilds_diagnostic_warnings_from_bounded_counts():
    result, control = run_plan({
        "candidates": (),
        "missing_path": 3,
        "read_failures": 2,
        "reference_failures": 1,
    }, ("Bearer PRIVATE_ACCESS_TOKEN", "/home/PRIVATE_USER/book.epub"))
    assert result.ok is False and result.changed == 0
    assert result.facts == {
        "checked": 0, "invalid": 0, "missing": 0,
        "read_failures": 2, "reference_failures": 1, "issues": {},
    }
    assert result.warnings == (
        "3 entrée(s) locale(s) sans chemin observable.",
        "2 artefact(s) sans métadonnées de référence : broker library.read en erreur.",
        "1 référence(s) d’intégrité invalide(s) : certains contrôles indisponibles.",
    )
    assert control.events == [
        {"message": "Aucun fichier local à vérifier"},
        {"message": "Audit d’intégrité terminé : aucun fichier"},
    ]
    exposed = json.dumps({
        "summary": result.summary, "warnings": result.warnings,
        "facts": result.facts, "events": control.events,
    }, ensure_ascii=False)
    assert "PRIVATE" not in exposed and "/home/" not in exposed and "Bearer" not in exposed
    assert control.checks == 2


def test_legacy_empty_plan_without_optional_counts_remains_compatible():
    result, control = run_plan({"candidates": ()})
    assert result.ok and result.warnings == ()
    assert result.facts["checked"] == 0
    assert control.checks == 2


@pytest.mark.parametrize("name,value", [
    ("read_failures", True),
    ("read_failures", -1),
    ("read_failures", 1_000_001),
    ("read_failures", "Bearer PRIVATE_TOKEN"),
    ("reference_failures", False),
    ("reference_failures", -4),
    ("reference_failures", "13"),
    ("missing_path", None),
    ("missing_path", 1_000_001),
    ("missing_path", ["PRIVATE_FILE"]),
])
def test_invalid_plan_counters_fail_closed_without_exposing_plan(name, value):
    result, control = run_plan({"candidates": (), name: value})
    assert not result.ok and result.changed == 0
    assert result.facts == {"invalid_plan": 1}
    assert result.warnings == ("Plan d’intégrité invalide : relancez l’analyse.",)
    assert control.events == [
        {"message": "Audit d’intégrité impossible : plan invalide"}
    ]
    assert control.checks == 2
    assert "PRIVATE" not in str(result)


@pytest.mark.parametrize("candidates", [
    "PRIVATE_FILE",
    {"path": "/home/PRIVATE_USER/book.epub"},
    None,
    True,
    [None] * 100_001,
])
def test_invalid_candidate_container_is_rejected_before_any_scan(candidates, monkeypatch):
    import cifereca_library_audits as audits

    monkeypatch.setattr(
        audits, "_file_integrity_issues",
        lambda *_a, **_kw: (_ for _ in ()).throw(AssertionError("unexpected file scan")),
    )
    result, control = run_plan({"candidates": candidates})
    assert result.ok is False and result.facts == {"invalid_plan": 1}
    assert control.events == [
        {"message": "Audit d’intégrité impossible : plan invalide"}
    ]


def test_wrong_tool_id_does_not_execute_or_replay_private_warnings():
    result, control = run_plan(
        {"candidates": ()},
        ("Bearer PRIVATE_TOKEN",),
        tool_id="PRIVATE_UNAUTHORIZED_TOOL",
    )
    assert not result.ok and result.facts == {"invalid_plan": 1}
    assert "PRIVATE" not in str(result.warnings)
    assert control.checks == 2
