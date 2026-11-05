"""#4048: no voluntary-return dispatch may remain in the GTK host.

These are negative, static acceptance tests. Historical receipts and
read-only evidence can remain in Core without a user-visible mutation.
"""
from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GUI = ROOT / "src/cifereca/gui"


def _functions(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        node.name for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def test_main_window_and_loan_page_have_no_return_dispatch():
    main = GUI / "main_window.py"
    controller = GUI / "loans_controller.py"
    retired = {
        "confirm_return", "_return_loan", "_return_loan_pnb",
        "_return_loan_result", "return_adept_from_detail",
    }
    assert not retired.intersection(_functions(main))
    assert not retired.intersection(_functions(controller))
    source = main.read_text(encoding="utf-8") + controller.read_text(encoding="utf-8")
    for forbidden in (
        "service.return_adept_acquisition(", "service.return_digital(",
        "service.return_loan_prefer_adept(", "loan_return_feedback(",
        'kind="adept-return"', 'kind="loan-return"',
    ):
        assert forbidden not in source


def test_voluntary_return_is_absent_but_physical_renewal_and_read_side_survive():
    controller = (GUI / "loans_controller.py").read_text(encoding="utf-8")
    main = (GUI / "main_window.py").read_text(encoding="utf-8")
    detail = (GUI / "detail_panel.py").read_text(encoding="utf-8")
    assert "def confirm_renew(" in controller
    assert "self.service.renew(loan, confirmed=True)" in controller
    assert "def confirm_renew(" in main
    assert "if paris_physical_renewal_visible(loan):" in detail
    assert "def refresh_loans(" in main
    assert "def reconcile_adept_from_detail(" in main


def test_gtk_e2e_has_no_legacy_return_confirmation():
    source = (ROOT / "e2e/diagnostics_navigation_probe.py").read_text(encoding="utf-8")
    assert "P1_4048_loan_return_controls_absent_no_mutation" in source
    assert "loan_sync_return_dialog" not in source
    assert 'w.confirm_return(' not in source


def test_successful_loan_download_never_reports_retired_return_capability():
    source = (GUI / "main_window.py").read_text(encoding="utf-8")
    # A file successfully recovered via ACSM must not be reported as an
    # ADEPT profile problem merely because voluntary loan return was retired.
    assert 'result.get("adept_return_available")' not in source
    assert "liaison ADEPT indisponible : voir le détail" not in source
    assert 'result_message = "Livre récupéré"' in source
    assert 'self.runner.submit("Acquisition du livre numérique…"' in source
