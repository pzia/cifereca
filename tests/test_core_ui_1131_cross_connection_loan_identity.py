"""P0 #1131 / #976: UI loan selection must retain exact Paris provenance."""
from __future__ import annotations

import ast
from dataclasses import replace
from pathlib import Path

from cifereca.models import Loan
from cifereca.core.loan_source_identity import loan_source_key, unique_library_loan


MAIN = Path(__file__).resolve().parents[1] / "src/cifereca/gui/main_window.py"
LOANS = Path(__file__).resolve().parents[1] / "src/cifereca/gui/loans_controller.py"


def _method(name):
    if name == "_library_selected":
        owner = ast.parse(MAIN.with_name("library_detail_controller.py").read_text(encoding="utf-8"))
        return ast.unparse(next(n for n in owner.body if isinstance(n, ast.FunctionDef)
                                and n.name == "library_selected"))
    moved = name in {"_populate_loans", "_render_loans", "_loan_selected"}
    tree = ast.parse((LOANS if moved else MAIN).read_text(encoding="utf-8"))
    scope = (tree.body if moved else next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "MainWindow").body)
    return ast.unparse(next(node for node in scope if isinstance(node, ast.FunctionDef)
                            and node.name == name))


def _loan(credential="paris-a", account="primary", identity="alice"):
    return Loan(
        id="same-paris-loan", provider="PNB", medium="digital",
        record_id="book-123", title="Un seul livre",
        source_provider="paris", source_credential=credential,
        source_account_id=account, identity_id=identity,
    )


def test_same_upstream_loan_from_two_connections_yields_distinct_row_keys():
    first = _loan()
    second = _loan("paris-b", identity="bob")
    assert first.id == second.id
    assert loan_source_key(first) != loan_source_key(second)
    assert len({loan_source_key(x): x for x in (first, second)}) == 2
    assert loan_source_key(first) == loan_source_key(replace(first, title="Titre édité"))


def test_borrower_subaccount_and_library_identity_are_part_of_row_key():
    primary = _loan()
    subaccount = replace(primary, source_account_id="linked-123")
    other_identity = replace(primary, identity_id="bob")
    assert len({loan_source_key(x) for x in (primary, subaccount, other_identity)}) == 3


def test_keys_are_opaque_and_do_not_leak_credentials_or_borrower_codes():
    loan = _loan(credential="secret-paris-token-123", account="secret-account-123")
    key = loan_source_key(loan)
    assert key.startswith("loan-source:")
    assert "secret" not in key and "token" not in key
    assert len(key) == len("loan-source:") + 64


def test_library_unqualified_duplicate_refuses_to_select_arbitrary_loan():
    first, second = _loan(), _loan("paris-b", identity="bob")
    assert unique_library_loan([first, second], {"loan_id": first.id}) is None
    assert unique_library_loan([second, first], {"loan_id": first.id}) is None


def test_exact_library_provenance_selects_one_loan_not_its_twin():
    first, second = _loan(), _loan("paris-b", identity="bob")
    item = {
        "loan_id": first.id, "provider_id": "paris",
        "item_id": first.record_id, "identity_id": "bob",
    }
    assert unique_library_loan([first, second], item) is second
    assert unique_library_loan([second, first], item) is second
    assert unique_library_loan([first, second], {**item, "source_credential": "paris-a"}) is None


def test_unique_legacy_library_loan_remains_selectable():
    loan = _loan()
    assert unique_library_loan([loan], {"loan_id": loan.id}) is loan
    assert unique_library_loan([loan], {"loan_id": "unknown"}) is None


def test_same_credential_but_different_borrower_is_not_inferred():
    a, b = _loan(account="primary"), _loan(account="linked-123")
    item = {"loan_id": a.id, "source_credential": "paris-a"}
    assert unique_library_loan([a, b], item) is None
    assert unique_library_loan([a, b], {**item, "source_account_id": "primary"}) is a


def test_main_window_uses_source_bound_keys_for_rows_and_details():
    populate = _method("_populate_loans")
    render = _method("_render_loans")
    selected = _method("_loan_selected")
    library = _method("_library_selected")
    assert "loan_source_key(x)" in populate
    assert "loan_source_key(loan)" in render
    assert "self.loan_objects.get(row.key)" in selected
    assert "unique_library_loan(window.loan_objects.values(), item)" in library
    assert "loan_objects.get(str(item.get('loan_id')))" not in library
