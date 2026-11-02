"""P1 #4048: obsolete GUI return notifications are no longer executable.

The old P0 #984 return-feedback oracle was for a GUI mutation withdrawn by
product decision. The retired implementation and tests remain in git history.
Retirement must not remove existing read-only ADEPT/loan diagnostics.
"""
from __future__ import annotations

import ast
from pathlib import Path


GUI = Path(__file__).resolve().parents[1] / "src" / "cifereca" / "gui"


def test_obsolete_return_notifier_is_not_importable_or_used_by_gtk():
    assert not (GUI / "return_feedback.py").exists()
    for module in ("main_window.py", "loans_controller.py", "detail_panel.py"):
        source = (GUI / module).read_text(encoding="utf-8")
        parsed = ast.parse(source)
        assert "loan_return_feedback" not in {
            node.id for node in ast.walk(parsed) if isinstance(node, ast.Name)
        }
        assert "from .return_feedback import" not in source
        assert "Restituer à Paris (PNB)" not in source


def test_loan_renewal_still_has_its_own_feedback_without_return_mutation():
    assert (GUI / "loan_renewal_feedback.py").is_file()
    controller = (GUI / "loans_controller.py").read_text(encoding="utf-8")
    assert "loan_renewal_feedback(result)" in controller
    assert "self.service.renew(loan, confirmed=True)" in controller
    assert "self.service.return_digital(" not in controller
    assert "self.service.return_loan_prefer_adept(" not in controller
