from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS

from cifereca.gui.loan_view import (
    LOAN_FILTER_LABELS,
    loan_context_label,
    loan_medium_counts,
)


ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "src/cifereca/gui/loans_controller.py"


def _method(name: str) -> str:
    tree = ast.parse(MAIN.read_text(encoding="utf-8"))
    method = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name)
    return ast.unparse(method)


def test_medium_counts_feed_all_three_segments():
    rows = [
        NS(medium="digital"),
        NS(medium="digital"),
        NS(medium="physical"),
    ]
    assert loan_medium_counts(rows) == {"all": 3, "physical": 1, "digital": 2}
    assert LOAN_FILTER_LABELS == {
        "all": "Tous",
        "physical": "Physiques",
        "digital": "Numériques",
    }


def test_context_label_distinguishes_filtered_scope_from_total():
    assert loan_context_label(
        filter_key="digital",
        visible_count=23,
        medium_count=23,
        total_count=30,
        language_filtered=False,
    ) == "23 numériques · 30 prêts"
    assert loan_context_label(
        filter_key="digital",
        visible_count=12,
        medium_count=23,
        total_count=30,
        language_filtered=True,
    ) == "12 affiché(s) · 23 numériques · 30 prêts"
    assert loan_context_label(
        filter_key="all",
        visible_count=18,
        medium_count=30,
        total_count=30,
        language_filtered=True,
    ) == "18 affiché(s) · 30 prêts"


def test_loans_page_keeps_segment_buttons_for_live_volume_labels():
    source = _method("_build_loans_page")
    assert "self.loan_filter_buttons" in source
    assert "self.loan_filter_buttons[value] = b" in source
    assert "self.loan_scope_count = Gtk.Label" in source


def test_render_updates_segment_volumes_and_context_without_changing_filter():
    source = _method("_render_loans")
    assert "counts = loan_medium_counts(all_loans)" in source
    assert "button.set_label(f'{label} · {counts.get(key, 0)}')" in source
    assert "loan_context_label(" in source
    assert "language_filtered=bool(wanted_languages)" in source
    assert "self.loan_page.show_rows(rows, total_count=len(rows))" in source
    assert "self.loan_filter =" not in source
