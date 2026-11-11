"""P0 #5055: provider-neutral display declaration admission for real loans."""
from pathlib import Path
import ast

import pytest

from cifereca.core.library_local_inspection import _declarable_right_scope


SCOPE = ("alice", "future-lender", "credential-a", "borrower-a", "edition-a")


@pytest.mark.parametrize("provider", ["bnf-pret", "paris", "future-lender"])
def test_only_active_identified_loans_can_be_declared(provider):
    scope = ("alice", provider, *SCOPE[2:])
    loan = {
        "entitlement_type": "loan", "state": "active",
        "loan_id": "loan-a",
    }
    assert _declarable_right_scope(loan, scope, "alice", 1)
    for invalid in (
        dict(loan, state="expired"), dict(loan, state="returned"),
        dict(loan, loan_id=None), dict(loan, loan_id=""),
        dict(loan, loan_id=True),
    ):
        assert not _declarable_right_scope(invalid, scope, "alice", 1)


@pytest.mark.parametrize("provider", ["bnf-pret", "paris", "future-lender"])
def test_non_loan_declarations_still_need_exact_identity_and_unique_source(provider):
    scope = ("alice", provider, *SCOPE[2:])
    right = {"entitlement_type": "owned", "state": "active"}
    assert _declarable_right_scope(right, scope, "alice", 1)
    assert not _declarable_right_scope(right, scope, "bob", 1)
    assert not _declarable_right_scope(right, scope, "alice", 2)
    assert not _declarable_right_scope(right, None, "alice", 1)
    assert not _declarable_right_scope(right, list(scope), "alice", 1)


def test_library_inspection_has_no_bnf_declaration_dispatch():
    source = (Path(__file__).resolve().parents[1]
              / "src/cifereca/core/library_local_inspection.py").read_text(
                  encoding="utf-8",
              )
    tree = ast.parse(source)
    assert not any(
        isinstance(node, ast.Compare)
        and any(isinstance(value, ast.Constant) and value.value == "bnf-pret"
                for value in (node.left, *node.comparators))
        for node in ast.walk(tree)
    )
    assert "bnf_exact_loan" not in source
    assert "_declarable_right_scope(" in source
