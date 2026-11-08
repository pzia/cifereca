"""P1 #4979: test the real loan GTK display adapter without a GTK runtime."""
from __future__ import annotations

import ast
from copy import copy
from pathlib import Path
from types import SimpleNamespace as NS

from cifereca.core.loan_cover_fallback import loan_local_cover
from cifereca.core.loan_source_identity import loan_source_key
from cifereca.models import Loan


PATH = Path(__file__).resolve().parents[1] / "src/cifereca/gui/loans_controller.py"


def ui_method():
    tree = ast.parse(PATH.read_text(encoding="utf-8"))
    owner = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_loan_display_cover"
    )
    namespace = {"Loan": Loan, "copy": copy, "loan_local_cover": loan_local_cover}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[owner], type_ignores=[])),
                 str(PATH), "exec"), namespace)
    return namespace["_loan_display_cover"]


def a_loan(*, identity="alice", image=None, account="account-a"):
    return Loan(
        id="loan-1", provider="paris", medium="digital",
        record_id="edition-1", title="Un titre",
        source_provider="bnf-pret", source_credential="card-a",
        source_account_id=account, identity_id=identity,
        thumbnail_url=image,
    )


def a_row(loan):
    return {
        "identity_id": loan.identity_id, "local": True,
        "artifact_id": "artifact-1", "placement_id": "placement-1",
        "_exact_loan_cover_proofs": [{
            "loan_source_key": loan_source_key(loan),
            "identity_id": loan.identity_id,
            "source_provider": loan.source_provider,
            "source_credential": loan.source_credential,
            "source_account_id": loan.source_account_id,
            "record_id": loan.record_id, "loan_id": loan.id,
            "artifact_id": "artifact-1", "placement_id": "placement-1",
            "tier": "embedded", "url": "file:///home/alice/.cache/cifereca/cover.png",
        }],
    }


def test_local_cover_changes_only_display_copy_not_actionable_loan():
    adapt = ui_method()
    loan = a_loan()
    host = NS(service=NS(current_identity=NS(identity_id="alice")),
              _library_entries_cache=[a_row(loan)])
    displayed = adapt(host, loan)
    assert displayed is not loan
    assert displayed.id == loan.id and displayed.source_account_id == loan.source_account_id
    assert displayed.thumbnail_url.endswith("/cover.png")
    assert loan.thumbnail_url is None


def test_supplier_cover_has_priority_and_never_gets_overwritten():
    adapt = ui_method()
    loan = a_loan(image="https://bnf.example/current.png")
    host = NS(service=NS(current_identity=NS(identity_id="alice")),
              _library_entries_cache=[a_row(loan)])
    assert adapt(host, loan) is loan
    assert loan.thumbnail_url == "https://bnf.example/current.png"


def test_cached_foreign_identity_and_unknown_borrower_do_not_leak_cover():
    adapt = ui_method()
    alice = a_loan()
    host = NS(service=NS(current_identity=NS(identity_id="bob")),
              _library_entries_cache=[a_row(alice)])
    assert adapt(host, alice) is alice
    host.service.current_identity.identity_id = "alice"
    assert adapt(host, a_loan(account="account-b")) is not alice
    assert adapt(host, a_loan(account="account-b")).thumbnail_url is None
    assert adapt(host, a_loan(account=None)).thumbnail_url is None
    host._library_entries_cache = []
    assert adapt(host, alice) is alice


def test_no_provider_io_or_mutation_in_loan_render_adapter():
    source = PATH.read_text(encoding="utf-8")
    method = ast.unparse(next(
        n for n in ast.parse(source).body
        if isinstance(n, ast.FunctionDef) and n.name == "_loan_display_cover"
    ))
    assert "refresh_identity_loans" not in method
    assert "service.library_entries" not in method
    assert "service.catalog_entries" not in method
    assert "copy(loan)" in method
    assert "loan.thumbnail_url =" not in method
    assert "loan_local_cover(" in method
    assert "show_loan(_loan_display_cover(self, loan))" in source
    assert "show_loan(_loan_display_cover(self, fresh))" in source
