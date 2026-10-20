"""P0 #1120: behavior-preserving extraction of the pre-fulfillment receipt.

Projections only: never load an ACSM, call ADEPT, mutate a receipt, or
consult a selected identity. The facade keeps the original locks/ordering.
"""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cifereca.core import loan_initial_receipt


@pytest.mark.parametrize("record_id", [None, "", "edition-123", 0])
def test_ledger_scope_preserves_borrower_and_exact_item_fallback(record_id):
    loan = NS(
        id="loan-777", record_id=record_id,
        source_provider="paris", source_credential="credential-private",
        source_account_id="borrower-private", title="A private title",
    )
    original = dict(
        entitlement_id="entitlement-private",
        provider_id=loan.source_provider,
        credential_id=loan.source_credential,
        account_id=loan.source_account_id,
        identity_id="identity-A",
        item_id=str(loan.record_id or loan.id),
        title=loan.title,
    )
    observed = loan_initial_receipt.ledger_base(
        loan, entitlement_id="entitlement-private", identity_id="identity-A",
    )
    assert observed == original
    assert list(observed) == list(original)
    assert observed["item_id"] == str(record_id or loan.id)
    # Never rewrite the private borrower or infer it from current GUI account.
    assert observed["account_id"] == "borrower-private"
    assert observed["identity_id"] == "identity-A"


def test_acquiring_receipt_preserves_payload_order_metadata_and_input():
    loan = NS(id="loan", record_id="record", source_provider="paris",
              source_credential="private", source_account_id="primary",
              title="Title")
    base = loan_initial_receipt.ledger_base(
        loan, entitlement_id=None, identity_id="identity-A",
    )
    meta = {"retained_acsm": "previous", "provider_loan_id": "old-loan"}
    before_base, before_meta = base.copy(), meta.copy()
    output = loan_initial_receipt.acquiring_receipt(base, loan, meta)
    expected = {
        **base, "status": "acquiring", "strategy": "loan_auto",
        "executor": "provider",
        "metadata": {**meta, "provider_loan_id": loan.id},
    }
    assert output == expected
    assert list(output) == list(expected)
    assert output["metadata"]["provider_loan_id"] == "loan"
    assert base == before_base and meta == before_meta
    assert output["metadata"] is not meta


def test_facade_keeps_acquisition_write_and_exception_guard_in_place():
    source = (Path(__file__).resolve().parents[1] /
              "src/cifereca/services.py").read_text()
    tree = ast.parse(source)
    cls = next(node for node in tree.body
               if isinstance(node, ast.ClassDef) and node.name == "LibraryService")
    method = next(node for node in cls.body
                  if isinstance(node, ast.FunctionDef)
                  and node.name == "_acquire_active_loan_automatically_guarded")
    rendered = ast.unparse(method)
    assert "loan_initial_receipt.ledger_base(" in rendered
    assert "loan_initial_receipt.acquiring_receipt(" in rendered
    assert "self.storage.acquisition_put(" in rendered
    assert "loan_provider_delivery.acquire_provider_loan(" in rendered
    assert rendered.index("loan_initial_receipt.acquiring_receipt(") < (
        rendered.index("loan_provider_delivery.acquire_provider_loan(")
    )
    assert "_AUTOMATIC_LOAN_GUARD" in source
