"""#3104: unknown loan due dates must not render contradictory GTK rows."""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS

from cifereca.core.loan_source_identity import loan_source_key
from cifereca.gui.loan_view import (
    LOAN_FILTER_LABELS, loan_context_label, loan_due_presentation,
    loan_medium_counts, loan_row_metadata,
)
from cifereca.models import Loan


class Row:
    def __init__(self, key, title, subtitle, meta, badge, thumbnail, *, badge_style=None):
        self.key, self.title, self.meta, self.badge = key, title, meta, badge
        self.badge_style = badge_style


def _render_actual(loans):
    path = Path(__file__).resolve().parents[1] / "src/cifereca/gui/loans_controller.py"
    method = next(node for node in ast.parse(path.read_text()).body
                  if isinstance(node, ast.FunctionDef) and node.name == "_render_loans")
    namespace = {
        "Loan": Loan,
        "BookListItem": Row,
        "loan_source_key": loan_source_key,
        "loan_due_presentation": loan_due_presentation,
        "loan_row_metadata": loan_row_metadata,
        "loan_medium_counts": loan_medium_counts,
        "loan_context_label": loan_context_label,
        "LOAN_FILTER_LABELS": LOAN_FILTER_LABELS,
    }
    module = ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[]))
    exec(compile(module, str(path), "exec"), namespace)
    rendered = []
    host = NS(
        service=NS(current_identity=NS(identity_id="alice"), last_loans=loans),
        loan_filter="all",
        loan_objects={},
        loan_page=NS(show_rows=lambda rows, **_kw: rendered.extend(rows)),
        _known_item_languages=lambda *a: set(),
        _attach_row_facets=lambda *a: None,
        search_controls=None,
    )
    namespace["_render_loans"](host)
    return {row.title: row for row in rendered}


def _loan(name, *, due_at=None, is_late=False, identity="alice"):
    return Loan(
        id=name, provider="BNF", medium="digital", record_id=name,
        title=name, document_type="Livre numérique", identity_id=identity,
        source_provider="bnf-pret", source_credential="card-A",
        due_at=due_at, is_late=is_late,
    )


def test_real_loan_row_projection_keeps_unknown_and_late_states_consistent():
    rendered = _render_actual([
        _loan("bnf-inconnue"),
        _loan("bnf-date-invalide", due_at="not-a-date"),
        _loan("bnf-en-retard", is_late=True),
        _loan("paris-date-attestee", due_at="2026-10-30"),
        _loan("autre-identite", identity="bob"),
    ])
    assert "autre-identite" not in rendered
    assert rendered["bnf-inconnue"].meta == "Livre numérique"
    assert rendered["bnf-inconnue"].badge == "Échéance inconnue"
    assert rendered["bnf-date-invalide"].meta == "Livre numérique"
    assert rendered["bnf-date-invalide"].badge == "Échéance inconnue"
    assert rendered["bnf-en-retard"].meta == "Livre numérique"
    assert rendered["bnf-en-retard"].badge == "En retard"
    assert rendered["bnf-en-retard"].badge_style == "loan-overdue"
    assert rendered["paris-date-attestee"].meta.startswith("Retour ")
    assert rendered["paris-date-attestee"].meta.endswith(" · Livre numérique")
    for row in rendered.values():
        assert "Retour —" not in row.meta


def test_pure_row_meta_only_mentions_return_when_date_was_parsed():
    unknown = loan_due_presentation(None)
    invalid = loan_due_presentation("not-a-date")
    late_without_date = loan_due_presentation(None, is_late=True)
    known = loan_due_presentation("2026-10-30")
    for due_at, due in ((None, unknown), ("not-a-date", invalid),
                        (None, late_without_date)):
        assert loan_row_metadata(
            due_at, due, document_type=None, provider="BNF"
        ) == "BNF"
    assert loan_row_metadata(
        "2026-10-30", known, document_type="Livre numérique", provider="BNF"
    ).endswith(" · Livre numérique")
