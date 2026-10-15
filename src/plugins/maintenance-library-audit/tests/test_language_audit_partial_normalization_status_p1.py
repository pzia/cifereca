"""#1494: evidence of failed normalization is not a successful completed audit.

All plans synthetic and read-only; no catalogue, files, network or GTK calls.
"""
from __future__ import annotations

import json

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


@pytest.mark.parametrize("facts,warnings,ok", (
    (
        {"language_values": 0, "invalid": 0, "noncanonical": 0,
         "malformed": 0, "normalization_failures": 0},
        (), True,
    ),
    (
        {"language_values": 1, "invalid": 1, "noncanonical": 0,
         "malformed": 0, "normalization_failures": 0},
        ("Langue invalide : notice n°1",), True,
    ),
    (
        {"language_values": 1, "invalid": 0, "noncanonical": 1,
         "malformed": 0, "normalization_failures": 0},
        ("Langue non canonique : notice n°1",), True,
    ),
    (
        {"language_values": 1, "invalid": 0, "noncanonical": 0,
         "malformed": 1, "normalization_failures": 0},
        ("Langue non textuelle : notice n°1",), True,
    ),
    (
        {"language_values": 1, "invalid": 0, "noncanonical": 0,
         "malformed": 0, "normalization_failures": 1},
        ("Langue non vérifiable : notice n°1",), False,
    ),
    (
        {"language_values": 2, "invalid": 0, "noncanonical": 1,
         "malformed": 2, "normalization_failures": 1},
        ("Langue non canonique : notice n°1",
         "Langue non vérifiable : notice n°2"), False,
    ),
))
def test_completed_only_when_every_core_normalization_was_observable(
    facts, warnings, ok,
):
    summary = (
        f'{facts["language_values"]} valeur(s) de langue ; '
        f'{facts["invalid"]} invalide(s), {facts["noncanonical"]} non canonique(s).'
    )
    if facts["malformed"] or facts["normalization_failures"]:
        summary += (
            f' {facts["malformed"]} valeur(s) non textuelle(s) ;'
            f' {facts["normalization_failures"]} normalisation(s) indisponible(s).'
        )
    plan = MaintenancePlan(
        tool_id=LanguageAuditTool.info.id,
        summary=summary,
        warnings=warnings,
        facts=facts,
    )
    control = Control()
    result = LanguageAuditTool().run(plan, control)
    assert result.ok is ok
    assert result.changed == 0
    assert result.summary == summary
    assert result.facts == facts
    assert result.warnings == warnings
    assert control.events == [{"message": summary}]
    assert control.checks == 2
    exported = json.dumps({
        "summary": result.summary, "warnings": result.warnings,
        "facts": result.facts, "events": control.events,
    }, ensure_ascii=False)
    assert "PRIVATE" not in exported and "Bearer" not in exported
