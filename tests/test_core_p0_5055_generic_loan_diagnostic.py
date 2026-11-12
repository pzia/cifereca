"""P0 #5055: terminal loan action is a closed Core fact, not a BnF protocol."""
from cifereca.core.diagnostic_explicit_loan_action import (
    ActionObservation, LoanActionError, public_fields, public_log_fields,
    log_message, valid_stage_code,
)
from cifereca.diagnostics import Diagnostics
from types import SimpleNamespace as NS


def test_synthetic_provider_terminal_is_typed_without_supplier_dispatch():
    diag = Diagnostics()
    host = NS(diagnostics=diag)
    ActionObservation(host, "stage_loan_token", provider_id="synthetic-lender").emit(
        "success", "candidate_acsm",
    )
    rows = diag.snapshot()
    assert len(rows) == 1
    detail = rows[0]["detail"]
    assert detail["provider_id"] == "synthetic-lender"
    assert detail["action"] == "stage_loan_token"
    assert detail["reason_code"] == "candidate_acsm"
    assert rows[0]["verified_loan_acsm_action"] is True
    assert public_log_fields(log_message(detail)) == detail


def test_unknown_or_foreign_reason_does_not_become_attested_terminal():
    valid = dict(
        schema_version=1, provider_id="synthetic-lender",
        action="stage_loan_token", phase="provider_delivery",
        status="failed", reason_code="synthetic_access_denied",
        duration_ms=11,
    )
    assert public_fields(valid) == valid
    assert not valid_stage_code("bnf_access_denied", "synthetic-lender")
    assert valid_stage_code("bnf_access_denied", "bnf-pret")
    for poison in (
        dict(valid, reason_code="bnf_access_denied"),
        dict(valid, reason_code="synthetic_SECRET"),
        dict(valid, provider_id="../../other"),
        dict(valid, duration_ms=True),
        dict(valid, task_ref="user-private-account"),
    ):
        assert public_fields(poison) == {}
        assert log_message(poison) is None
    assert LoanActionError("PRIVATE signed://token").code == "loan_action_failed"


def test_generic_plugin_diagnostic_cannot_claim_core_action_attestation():
    diag = Diagnostics()
    value = dict(
        schema_version=1, provider_id="synthetic-lender",
        action="stage_loan_token", phase="provider_delivery",
        status="failed", reason_code="synthetic_access_denied", duration_ms=11,
    )
    diag.add("LOAN", "loan_acsm_action", "failed", detail=value)
    unverified = diag.snapshot()[0]
    assert not unverified.get("verified_loan_acsm_action")
    diag.add_core_loan_acsm_action(value)
    assert diag.snapshot()[-1]["verified_loan_acsm_action"] is True


def test_all_three_supplier_specific_core_gui_modules_are_physically_gone():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    retired = (
        "src/cifereca/gui/bnf_borrow_controller.py",
        "src/cifereca/gui/bnf_acsm_action_controller.py",
        "src/cifereca/core/diagnostic_bnf_loan_action.py",
    )
    assert all(not (root / path).exists() for path in retired)
    for path in (
        "src/cifereca/gui/manual_borrow_controller.py",
        "src/cifereca/gui/explicit_loan_acsm_controller.py",
        "src/cifereca/core/diagnostic_explicit_loan_action.py",
    ):
        text = (root / path).read_text(encoding="utf-8")
        assert "bnf-pret" not in text
        assert "cifereca_bnf" not in text
    for path in (root / "src/cifereca").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "from .bnf_borrow_controller" not in text
        assert "from .bnf_acsm_action_controller" not in text
        assert "from .diagnostic_bnf_loan_action" not in text
