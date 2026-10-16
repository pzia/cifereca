"""#1496 — restored naming preview must agree with measured diagnostics.

Synthetic plans only; no broker, filesystem, GTK or network access.
"""
from cifereca.plugin_api import MaintenancePlan
from cifereca_library_audits import NamingAuditTool
import pytest


class Control:
    def __init__(self):
        self.events = []
        self.checks = 0

    def check_cancelled(self):
        self.checks += 1

    def report(self, **event):
        self.events.append(event)


def run(*, checked=2, mismatch=1, unavailable=0, policy_failures=1, warnings):
    tool = NamingAuditTool()
    plan = MaintenancePlan(
        tool_id=tool.info.id,
        summary=f"{mismatch} nom(s) différent(s) de la politique Core sur {checked} fichier(s) vérifiable(s).",
        warnings=warnings,
        facts={
            "checked": checked, "mismatch": mismatch,
            "unavailable": unavailable, "policy_failures": policy_failures,
        },
    )
    control = Control()
    return tool.run(plan, control), control


@pytest.mark.parametrize("warnings", (
    # Both classes required for the 2 sampled issues.
    ("Nom non conforme : notice n°1", "Audit uniquement : aucune mutation de placement n'est effectuée."),
    # Cannot duplicate a notice, even across two different issue classes.
    ("Nom non conforme : notice n°2", "Politique de nommage indisponible : notice n°2",
     "1 entrée(s) sans proposition valide de la politique Core.",
     "Audit uniquement : aucune mutation de placement n'est effectuée."),
    # The scan produces samples in strictly increasing notice order.
    ("Politique de nommage indisponible : notice n°3", "Nom non conforme : notice n°1",
     "1 entrée(s) sans proposition valide de la politique Core.",
     "Audit uniquement : aucune mutation de placement n'est effectuée."),
    # Repeated aggregate / missing mandatory aggregate are not a valid preview.
    ("Nom non conforme : notice n°1", "Politique de nommage indisponible : notice n°2",
     "Audit uniquement : aucune mutation de placement n'est effectuée."),
    ("Nom non conforme : notice n°1", "Politique de nommage indisponible : notice n°2",
     "1 entrée(s) sans proposition valide de la politique Core.",
     "1 entrée(s) sans proposition valide de la politique Core.",
     "Audit uniquement : aucune mutation de placement n'est effectuée."),
))
def test_cached_naming_preview_inconsistent_warnings_fail_closed(warnings):
    result, control = run(warnings=warnings)
    assert result.ok is False
    assert result.facts == {"invalid_plan": 1}
    assert result.warnings == ("Plan d’audit des noms invalide : relancez l’analyse.",)
    assert control.events == [{"message": "Audit des noms impossible : plan invalide"}]
    assert control.checks == 2


def test_valid_naming_preview_keeps_private_metadata_out_of_progress():
    result, control = run(
        checked=2, mismatch=1, unavailable=1, policy_failures=1,
        warnings=(
            "Nom non conforme : notice n°1",
            "Politique de nommage indisponible : notice n°3",
            "1 entrée(s) sans nom/titre public vérifiable.",
            "1 entrée(s) sans proposition valide de la politique Core.",
            "Audit uniquement : aucune mutation de placement n'est effectuée.",
        ),
    )
    assert result.ok is False  # Core naming policy not fully available
    assert result.facts["unavailable"] == 1
    assert result.changed == 0
    assert control.events == [{"message": result.summary}]


def test_preview_cap_keeps_first_twelve_samples_and_aggregate():
    warnings = tuple(f"Nom non conforme : notice n°{n}" for n in range(1, 13))
    result, control = run(
        checked=20, mismatch=20, policy_failures=0,
        warnings=warnings + ("Audit uniquement : aucune mutation de placement n'est effectuée.",),
    )
    assert result.ok is True
    assert result.warnings[:12] == warnings
    assert result.facts["mismatch"] == 20
    assert control.events == [{"message": result.summary}]


def test_zero_entries_have_only_readonly_footer_not_false_fraction():
    result, control = run(
        checked=0, mismatch=0, policy_failures=0,
        warnings=("Audit uniquement : aucune mutation de placement n'est effectuée.",),
    )
    assert result.ok
    assert control.events == [{"message": result.summary}]
