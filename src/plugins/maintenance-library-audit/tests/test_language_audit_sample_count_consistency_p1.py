"""#1494: restored language-audit samples cannot contradict their counters.

Only synthetic plans; no source fetching, GTK or filesystem mutation.
"""
import pytest

from cifereca.plugin_api import MaintenancePlan
from cifereca_library_audits import LanguageAuditTool


class Control:
    def __init__(self):
        self.events = []
        self.checks = 0

    def check_cancelled(self):
        self.checks += 1

    def report(self, **event):
        self.events.append(event)


def execute(*, values=2, invalid=0, noncanonical=0, malformed=0,
            normalization_failures=0, warnings=()):
    summary = (
        f"{values} valeur(s) de langue ; {invalid} invalide(s), "
        f"{noncanonical} non canonique(s)."
    )
    if malformed or normalization_failures:
        summary += (
            f" {malformed} valeur(s) non textuelle(s) ;"
            f" {normalization_failures} normalisation(s) indisponible(s)."
        )
    tool = LanguageAuditTool()
    plan = MaintenancePlan(
        tool_id=tool.info.id,
        summary=summary,
        warnings=warnings,
        facts={
            "language_values": values, "invalid": invalid,
            "noncanonical": noncanonical, "malformed": malformed,
            "normalization_failures": normalization_failures,
        },
    )
    control = Control()
    return tool.run(plan, control), control


@pytest.mark.parametrize("warnings", (
    ("Langue non canonique : notice n°1",),  # noncanonical=0
    ("Langue non textuelle : notice n°1",),  # malformed=0
    ("Langue invalide : notice n°2", "Langue invalide : notice n°2"),  # count=1
    ("Langue invalide : notice n°3", "Langue non canonique : notice n°2"),
))
def test_forged_language_preview_fails_closed(warnings):
    result, control = execute(
        values=2, invalid=1, noncanonical=0, warnings=warnings,
    )
    assert result.ok is False
    assert result.facts == {"invalid_plan": 1}
    assert control.events == [{"message": "Audit des langues impossible : plan invalide"}]
    assert control.checks == 2


def test_language_samples_must_remain_in_scan_order():
    result, control = execute(
        values=2, invalid=1, noncanonical=1,
        warnings=(
            "Langue non canonique : notice n°3",
            "Langue invalide : notice n°2",
        ),
    )
    assert result.ok is False
    assert result.facts == {"invalid_plan": 1}
    assert control.events == [{"message": "Audit des langues impossible : plan invalide"}]


def test_multiple_unreadable_values_on_one_notice_count_once_in_preview():
    result, control = execute(
        values=1, invalid=1, malformed=2,
        warnings=(
            "Langue non textuelle : notice n°1",
            "Langue invalide : notice n°1",
        ),
    )
    assert result.ok is True  # classified findings, no Core normalization failure
    assert result.facts["malformed"] == 2
    assert len(result.warnings) == 2
    assert control.events == [{"message": result.summary}]


def test_partial_language_samples_keep_legacy_counted_findings():
    result, control = execute(values=3, invalid=1, warnings=())
    assert result.ok is True
    assert result.facts["invalid"] == 1
    assert result.warnings == ()
    assert control.events == [{"message": result.summary}]


def test_clean_language_plan_remains_successful_without_fabricated_progress():
    result, control = execute()
    assert result.ok is True
    assert result.warnings == ()
    assert control.events == [{"message": result.summary}]
