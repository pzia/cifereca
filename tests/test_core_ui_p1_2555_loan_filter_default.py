"""P1 #2555: physical loans must not disappear on first opening."""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

from cifereca.gui.loan_view import loan_medium_counts, loan_context_label


_SOURCE = (Path(__file__).resolve().parents[1] /
           "src/cifereca/gui/loans_controller.py").read_text(encoding="utf-8")


def _function(name):
    tree = ast.parse(_SOURCE)
    return next(node for node in tree.body
                if isinstance(node, ast.FunctionDef) and node.name == name)


def test_all_is_selected_before_any_refresh_even_with_physical_only_fixture():
    function = _function("_build_loans_page")
    source = ast.unparse(function)
    assert "self.loan_filter = 'all'" in source
    assert "if value == 'all':" in source
    assert "b.set_active(True)" in source
    assert "self.loan_filter = 'digital'" not in source
    rows = [SimpleNamespace(medium="physical"),
            SimpleNamespace(medium="physical")]
    counts = loan_medium_counts(rows)
    assert counts == {"all": 2, "physical": 2, "digital": 0}
    assert loan_context_label(filter_key="all", visible_count=2,
                              medium_count=2, total_count=2,
                              language_filtered=False) == "2 prêts"


def test_empty_text_does_not_claim_source_has_zero_loans():
    source = ast.unparse(_function("_build_loans_page"))
    assert "Aucun emprunt affiché" in source
    assert "ne prouve pas l’absence de prêts" in source
    assert "sans emprunt ni retour" in source


def test_explicit_digital_filter_and_language_filters_are_preserved():
    source = ast.unparse(_function("_build_loans_page"))
    render = ast.unparse(_function("_render_loans"))
    assert "('digital', 'Numériques')" in source
    assert "self.loan_languages = MultiSelect" in source
    assert "if self.loan_filter != 'all' and loan.medium != self.loan_filter" in render
    assert "loan_source_key(loan)" in render
    assert "language_filtered=bool(wanted_languages)" in render
    assert "self.loan_filter =" not in render
