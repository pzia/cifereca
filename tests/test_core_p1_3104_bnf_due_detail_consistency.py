"""#3104: exact loan detail must display 'unknown', never raw/bogus due.

Compiles the REAL DetailPanel.show_loan method without importing GTK; a
fake presentation sink stops after the read-only detail rows are projected.
No network, scheduler, loan action or provider-specific branching.
"""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cifereca.core.search import file_format
from cifereca.gui.detail_model import (
    compact_availability_label, detail_language, fr_date,
)
from cifereca.gui.loan_view import loan_due_detail_value
from cifereca.models import Loan


class _Projected(Exception):
    pass


def _detail(loan: Loan):
    path = Path(__file__).resolve().parents[1] / "src/cifereca/gui/detail_panel.py"
    module = ast.parse(path.read_text(encoding="utf-8"))
    detail_cls = next(
        node for node in module.body
        if isinstance(node, ast.ClassDef) and node.name == "DetailPanel"
    )
    method = next(
        node for node in detail_cls.body
        if isinstance(node, ast.FunctionDef) and node.name == "show_loan"
    )
    namespace = {
        "Loan": Loan,
        "book_detail_presentation": lambda **kwargs: kwargs,
        "file_format": file_format,
        "detail_language": detail_language,
        "compact_availability_label": compact_availability_label,
        "loan_due_detail_value": loan_due_detail_value,
        "fr_date": fr_date,
    }
    tree = ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[]))
    exec(compile(tree, str(path), "exec"), namespace)

    class Host:
        window = NS(
            service=NS(provider_display_name=lambda name: "BnF Prêt"),
        )
        presentation = None

        def _cancel_meta_request(self):
            return None

        def show_presentation(self, presentation):
            self.presentation = presentation
            raise _Projected

    host = Host()
    with pytest.raises(_Projected):
        namespace["show_loan"](host, loan, include_pnb_action=False)
    assert host.presentation is not None
    loan_group = next(rows for label, rows in host.presentation["section_rows"]
                      if label == "Prêt")
    return dict(loan_group), host.presentation


def _loan(name, due_at=None, *, is_late=False, provider="bnf-pret"):
    return Loan(
        id=name, provider="BNF", medium="digital", record_id=name, title=name,
        source_provider=provider, source_credential="card-A",
        identity_id="alice", author="Autrice observée",
        due_at=due_at, is_late=is_late,
    )


@pytest.mark.parametrize("raw", [None, "", "not-a-date", "2026-99-99"])
def test_bnf_missing_or_malformed_due_is_explicitly_unknown_in_real_detail(raw):
    data, presentation = _detail(_loan("Carte BnF 1", raw))
    assert data["Retour prévu"] == "Échéance inconnue"
    assert presentation["subtitle"] == "Autrice observée"
    assert data["Source"] if "Source" in data else True


def test_known_date_and_explicit_late_without_date_remain_distinct():
    known, _ = _detail(_loan("Carte BnF 2", "2026-12-05"))
    assert known["Retour prévu"] == "05/12/2026"
    late, _ = _detail(_loan("Carte BnF 3", is_late=True))
    assert late["Retour prévu"] == "En retard"
    assert late["Retour prévu"] != "Retour aujourd’hui"


def test_shared_non_bnf_loan_detail_uses_same_unknown_label():
    data, _ = _detail(_loan("Carte Paris", provider="paris"))
    assert data["Retour prévu"] == "Échéance inconnue"


def test_pure_detail_formatter_refuses_unparseable_date():
    assert loan_due_detail_value("bad") == "Échéance inconnue"
    assert loan_due_detail_value(None) == "Échéance inconnue"
    assert loan_due_detail_value(None, is_late=True) == "En retard"
    assert loan_due_detail_value("2026-12-05") == "05/12/2026"
