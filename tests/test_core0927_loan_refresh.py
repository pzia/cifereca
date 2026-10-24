from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace


SOURCE = Path(__file__).resolve().parents[1] / "src/cifereca/gui/loans_controller.py"


def _method(name: str):
    tree = ast.parse(SOURCE.read_text())
    fn = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name)
    ns = {}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), str(SOURCE), "exec"), ns)
    return ns[name]


class _Detail:
    def __init__(self):
        self.shown = []
        self.empty_calls = []

    def show_loan(self, loan):
        self.shown.append(loan)

    def empty(self, *args):
        self.empty_calls.append(args)


def _loan(**overrides):
    values = dict(id="loan-1", source_provider="paris", source_credential="card", source_account_id="account")
    values.update(overrides)
    return SimpleNamespace(**values)


def test_refresh_replaces_displayed_loan_with_fresh_instance():
    previous = _loan()
    fresh = _loan()
    detail = _Detail()
    populated = []
    toasts = []
    host = SimpleNamespace(
        _displayed_loan=previous,
        _populate_loans=lambda rows: populated.append(rows),
        loan_page=SimpleNamespace(detail=detail),
        toast=lambda message: toasts.append(message),
    )

    _method("_loans_refreshed")(host, [fresh])

    assert populated == [[fresh]]
    assert host._displayed_loan is fresh
    assert detail.shown == [fresh]
    assert detail.empty_calls == []
    assert toasts == ["1 emprunt(s) actualisé(s)"]


def test_refresh_clears_detail_when_provider_no_longer_lists_loan():
    previous = _loan()
    detail = _Detail()
    host = SimpleNamespace(
        _displayed_loan=previous,
        _populate_loans=lambda rows: None,
        loan_page=SimpleNamespace(detail=detail),
        toast=lambda message: None,
    )

    _method("_loans_refreshed")(host, [])

    assert host._displayed_loan is None
    assert detail.shown == []
    assert len(detail.empty_calls) == 1
    title, message = detail.empty_calls[0]
    assert "non retrouvé" in title.lower()
    assert "n’est pas confirmée" in message
    assert "fichiers locaux" in message.lower()


def test_refresh_matches_full_provider_loan_identity_not_title_only():
    previous = _loan(id="same", source_account_id="account-a")
    wrong_account = _loan(id="same", source_account_id="account-b")
    fresh = _loan(id="same", source_account_id="account-a")
    detail = _Detail()
    host = SimpleNamespace(
        _displayed_loan=previous,
        _populate_loans=lambda rows: None,
        loan_page=SimpleNamespace(detail=detail),
        toast=lambda message: None,
    )

    _method("_loans_refreshed")(host, [wrong_account, fresh])

    assert host._displayed_loan is fresh
    assert detail.shown == [fresh]


def test_refresh_zero_rows_explains_uncertainty_without_claiming_no_provider_loans():
    toasts = []
    host = SimpleNamespace(
        _displayed_loan=None,
        _populate_loans=lambda rows: None,
        loan_page=SimpleNamespace(detail=_Detail()),
        toast=lambda message: toasts.append(message),
    )

    _method("_loans_refreshed")(host, [])

    assert toasts == [
        "Aucun emprunt reçu · Vérifiez l’identité, les sources et les filtres"
    ]
    assert not any("0 emprunt(s) actualisé(s)" in item for item in toasts)
