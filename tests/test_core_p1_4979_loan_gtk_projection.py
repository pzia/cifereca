"""P1 #4979: test the real loan GTK display adapter without a GTK runtime."""
from __future__ import annotations

import ast
from copy import copy
from pathlib import Path
from types import SimpleNamespace as NS

from cifereca.core.covers import candidate
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
    namespace = {"Loan": Loan, "copy": copy, "loan_local_cover": loan_local_cover,
                 "candidate": candidate}
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
        "thumbnail_url": "file:///home/alice/.cache/cifereca/cover.png",
        "metadata": {"cover_resolution": {
            "status": "selected", "tier": "embedded",
            "url": "file:///home/alice/.cache/cifereca/cover.png",
        }},
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


def test_exact_embedded_loan_cover_beats_valid_supplier_thumbnail():
    """An attested local Placement takes priority without mutating the loan."""
    adapt = ui_method()
    loan = a_loan(image="https://bnf.example/current.png")
    host = NS(service=NS(current_identity=NS(identity_id="alice")),
              _library_entries_cache=[a_row(loan)])
    displayed = adapt(host, loan)
    assert displayed is not loan
    assert displayed.thumbnail_url == "file:///home/alice/.cache/cifereca/cover.png"
    assert displayed.source_account_id == loan.source_account_id
    assert loan.thumbnail_url == "https://bnf.example/current.png"
    assert adapt(host, loan).thumbnail_url == displayed.thumbnail_url


def test_supplier_cover_remains_when_no_stronger_exact_proof():
    adapt = ui_method()
    loan = a_loan(image="https://bnf.example/current.png")
    host = NS(service=NS(current_identity=NS(identity_id="alice")),
              _library_entries_cache=[])
    assert adapt(host, loan) is loan

    # Even a proven *other* catalog candidate cannot displace a direct
    # already-valid thumbnail from this exact loan.
    row = a_row(loan)
    catalog_url = "https://public.example/catalog.jpg"
    row["thumbnail_url"] = catalog_url
    row["metadata"]["cover_resolution"].update(tier="catalog", url=catalog_url)
    row["_exact_loan_cover_proofs"][0].update(tier="catalog", url=catalog_url)
    host._library_entries_cache = [row]
    assert adapt(host, loan) is loan
    row["metadata"]["cover_resolution"]["status"] = "missing"
    assert adapt(host, loan) is loan
    assert loan.thumbnail_url == "https://bnf.example/current.png"


def test_exact_origin_outranks_supplier_catalog_and_bad_proof_does_not():
    adapt = ui_method()
    loan = a_loan(image="https://bnf.example/current.png")
    row = a_row(loan)
    origin_url = "https://public.example/source-cover.jpg"
    row["thumbnail_url"] = origin_url
    row["metadata"]["cover_resolution"].update(tier="origin", url=origin_url)
    row["_exact_loan_cover_proofs"][0].update(tier="origin", url=origin_url)
    host = NS(service=NS(current_identity=NS(identity_id="alice")),
              _library_entries_cache=[row])
    assert adapt(host, loan).thumbnail_url == origin_url
    row["_exact_loan_cover_proofs"][0]["placement_id"] = "wrong-placement"
    assert adapt(host, loan) is loan


def test_ambiguous_or_cross_account_embedded_cannot_replace_supplier():
    from copy import deepcopy
    adapt = ui_method()
    loan = a_loan(image="https://bnf.example/current.png")
    one = a_row(loan)
    other = deepcopy(one)
    other["placement_id"] = "placement-2"
    other["_exact_loan_cover_proofs"][0]["placement_id"] = "placement-2"
    second_url = "file:///home/alice/.cache/cifereca/second.png"
    other["thumbnail_url"] = second_url
    other["metadata"]["cover_resolution"]["url"] = second_url
    other["_exact_loan_cover_proofs"][0]["url"] = second_url
    host = NS(service=NS(current_identity=NS(identity_id="alice")),
              _library_entries_cache=[one, other])
    assert adapt(host, loan) is loan  # equal embedded strengths: ambiguous

    other_account = a_loan(account="account-b", image=loan.thumbnail_url)
    host._library_entries_cache = [one]
    assert adapt(host, other_account) is other_account
    host.service.current_identity.identity_id = "bob"
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


def test_untrusted_supplier_cover_never_prevents_exact_local_fallback():
    adapt = ui_method()
    for private in (
        "https://cdn.example/covers/book.jpg?token=PRIVATE",
        "http://127.0.0.1/admin",
        "https://reader.local/book.jpg",
        "file:///home/another-user/private/cover.jpg",
        "https://cdn.example/book.jpg#PRIVATE",
    ):
        original = a_loan(image=private)
        host = NS(service=NS(current_identity=NS(identity_id="alice")),
                  _library_entries_cache=[a_row(original)])
        visible = adapt(host, original)
        assert visible is not original
        assert visible.thumbnail_url == "file:///home/alice/.cache/cifereca/cover.png"
        assert original.thumbnail_url == private
        assert visible.id == original.id


def test_untrusted_supplier_url_without_attested_fallback_is_hidden():
    adapt = ui_method()
    original = a_loan(image="https://cdn.example/cover.jpg?access_token=PRIVATE")
    host = NS(service=NS(current_identity=NS(identity_id="alice")),
              _library_entries_cache=[])
    visible = adapt(host, original)
    assert visible is not original
    assert visible.thumbnail_url is None
    assert original.thumbnail_url.endswith("PRIVATE")
