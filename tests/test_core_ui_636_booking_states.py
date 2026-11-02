"""#4147: bookings UI is retired; historical Booking model is v1-only."""
from __future__ import annotations

from pathlib import Path
import ast

from cifereca.gui import secondary_page_controller, secondary_results_controller


ROOT = Path(__file__).resolve().parents[1] / "src/cifereca/gui"


def test_no_live_reservation_presenters_or_detail_methods():
    assert not (ROOT / "booking_view.py").exists()
    assert not hasattr(secondary_page_controller, "build_bookings_page")
    assert not hasattr(secondary_results_controller, "populate_bookings")
    for file in ("main_window.py", "detail_panel.py"):
        tree = ast.parse((ROOT / file).read_text(encoding="utf-8"))
        bodies = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
        assert not any(n.name in {
            "refresh_bookings", "_build_bookings_page", "_populate_bookings",
            "_booking_selected", "show_booking",
        } for n in bodies)


def test_loan_and_history_presenters_remain():
    from cifereca.gui.loan_view import loan_due_presentation
    assert callable(loan_due_presentation)
    assert callable(secondary_page_controller.build_history_page)
    assert callable(secondary_results_controller.populate_history)
    assert callable(secondary_results_controller.history_scroll_changed)
