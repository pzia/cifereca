"""#1493: cover-audit cached preview must agree with counted findings.

Synthetic plans only; no GTK, provider, filesystem, URL fetch or mutation.
"""
from cifereca.plugin_api import MaintenancePlan
from cifereca_library_audits import CoverAuditTool

import pytest


class Control:
    def __init__(self):
        self.events = []
        self.checks = 0

    def check_cancelled(self):
        self.checks += 1

    def report(self, **event):
        self.events.append(event)


def execute(*, entries, missing, invalid, warnings):
    tool = CoverAuditTool()
    plan = MaintenancePlan(
        tool_id=tool.info.id,
        summary=(
            f"{missing} notice(s) sans couverture, "
            f"{invalid} référence(s) suspecte(s), sur {entries} notice(s)."
        ),
        warnings=warnings,
        facts={"entries": entries, "missing": missing, "invalid": invalid},
    )
    control = Control()
    return tool.run(plan, control), control


@pytest.mark.parametrize("warnings", (
    ("Sans couverture : notice n°1", "Sans couverture : notice n°2"),
    ("Sans couverture : notice n°1", "Sans couverture : notice n°1"),
    ("Couverture non reconnue : notice n°3", "Sans couverture : notice n°1"),
    ("Sans couverture : notice n°1",),
    (),
))
def test_untrusted_preview_with_inconsistent_sample_fails_closed(warnings):
    result, control = execute(entries=3, missing=1, invalid=1, warnings=warnings)
    assert result.ok is False
    assert result.changed == 0
    assert result.facts == {"invalid_plan": 1}
    assert result.warnings == ("Plan de couvertures invalide : relancez l’analyse.",)
    assert control.events == [{"message": "Audit des couvertures impossible : plan invalide"}]
    assert control.checks == 2


def test_bounded_first_twelve_sample_remains_accepted_for_large_scan():
    warnings = tuple(f"Sans couverture : notice n°{i}" for i in range(1, 13))
    result, control = execute(
        entries=19, missing=15, invalid=0, warnings=warnings,
    )
    assert result.ok is True
    assert result.facts == {"entries": 19, "missing": 15, "invalid": 0}
    assert result.warnings == warnings
    assert len(control.events) == 1
    assert "total" not in control.events[0]


def test_empty_cover_scan_accepts_no_samples_without_synthetic_progress():
    result, control = execute(entries=0, missing=0, invalid=0, warnings=())
    assert result.ok is True
    assert result.warnings == ()
    assert control.events == [{"message": result.summary}]
