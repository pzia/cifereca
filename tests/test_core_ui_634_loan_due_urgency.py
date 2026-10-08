from __future__ import annotations

import ast
from datetime import date
from pathlib import Path

from cifereca.gui.loan_view import loan_due_presentation


ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "src/cifereca/gui/main_window.py"
WIDGETS = ROOT / "src/cifereca/gui/book_widgets.py"


def _method(path: Path, class_name: str, method_name: str) -> str:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name)
    method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == method_name)
    return ast.unparse(method)


def test_due_labels_translate_time_to_actionable_french():
    today = date(2026, 10, 8)
    assert loan_due_presentation("2026-10-05", today=today).label == "En retard de 3 jours"
    assert loan_due_presentation("2026-10-07", today=today).label == "En retard de 1 jour"
    assert loan_due_presentation("2026-10-08", today=today).label == "Retour aujourd’hui"
    assert loan_due_presentation("2026-10-09", today=today).label == "Retour demain"
    assert loan_due_presentation("2026-10-13", today=today).label == "Retour dans 5 jours"


def test_due_styles_distinguish_overdue_imminent_and_normal():
    today = date(2026, 10, 8)
    assert loan_due_presentation("2026-10-07", today=today).style == "loan-overdue"
    assert loan_due_presentation("2026-10-10", today=today).style == "loan-imminent"
    assert loan_due_presentation("2026-10-20", today=today).style == "loan-normal"


def test_due_sort_prioritizes_overdue_then_nearest_deadlines():
    today = date(2026, 10, 8)
    values = [
        loan_due_presentation("2026-10-20", today=today),
        loan_due_presentation("2026-10-09", today=today),
        loan_due_presentation("2026-10-01", today=today),
        loan_due_presentation("2026-10-08", today=today),
    ]
    assert [item.label for item in sorted(values, key=lambda item: item.sort_key)] == [
        "En retard de 7 jours",
        "Retour aujourd’hui",
        "Retour demain",
        "Retour dans 12 jours",
    ]


def test_loan_list_sorts_only_after_existing_filters():
    source = _method(MAIN, "MainWindow", "_render_loans")
    assert "wanted_languages" in source
    assert "if self.loan_filter != 'all' and loan.medium != self.loan_filter" in source
    assert "loan_due_presentation" in source
    assert "ranked_rows.sort(key=lambda item: item[0])" in source
    assert "Retour {fr_date(loan.due_at)}" in source


def test_badge_style_is_recycled_safely_between_rows():
    bind = _method(WIDGETS, "BookListCell", "bind")
    unbind = _method(WIDGETS, "BookListCell", "unbind")
    assert "loan-overdue" in bind
    assert "loan-imminent" in bind
    assert "loan-normal" in bind
    assert "self.badge_label.add_css_class(badge_style)" in bind
    assert "self.badge_label.remove_css_class(css_class)" in unbind
