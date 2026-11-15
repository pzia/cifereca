"""#6168: Emprunts list/detail share N1/N2 decision per exact borrower revision."""
from __future__ import annotations

import ast
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace as NS

from cifereca.core.book_cover_source_projection import exact_loan_cover_scope
from cifereca.core.loan_source_identity import (
    loan_source_key, unambiguous_loan_rows,
)
from cifereca.gui import loan_cover_decision_bridge as bridge
from cifereca.models import Loan
from tests.test_core_p1_4979_loan_gtk_projection import (
    a_loan, a_row, ui_method,
)
from tests.test_core_p1_4979_late_library_cover_repaint import (
    host, snapshot_method,
)


SOURCE = Path(__file__).resolve().parents[1] / "src/cifereca/gui/loans_controller.py"


def test_existing_loan_list_and_detail_reuse_one_n1_decision(monkeypatch):
    obj, loan, views = host()
    row = a_row(loan)
    calls = []
    original = bridge.project_exact_loan_cover

    def counted(*args, **kwargs):
        calls.append(loan_source_key(args[0]))
        return original(*args, **kwargs)

    monkeypatch.setattr(bridge, "project_exact_loan_cover", counted)
    snapshot_method()(obj, [row])
    assert views[-1] == row["thumbnail_url"]
    assert len(calls) == 1
    list_copy = ui_method()(obj, loan)
    detail_copy = ui_method()(obj, loan)
    assert list_copy.thumbnail_url == detail_copy.thumbnail_url == row["thumbnail_url"]
    assert len(calls) == 1  # Neither view rescans the local Library.
    assert loan.thumbnail_url is None
    scope = exact_loan_cover_scope(loan)
    cache = obj._loan_cover_decision_cache
    decision = cache.peek(scope, revision=obj._loan_cover_decision_revision)
    assert decision.status == "selected"
    assert decision.tier == "embedded"
    assert decision.uri == row["thumbnail_url"]


def test_later_library_cover_revision_replaces_old_n2_decision():
    obj, loan, views = host()
    first = a_row(loan)
    snapshot_method()(obj, [first])
    scope = exact_loan_cover_scope(loan)
    cache = obj._loan_cover_decision_cache
    initial_rev = obj._loan_cover_decision_revision
    initial = cache.peek(scope, revision=initial_rev)
    second = deepcopy(first)
    newer = "file:///home/alice/.cache/cifereca/new.png"
    second["thumbnail_url"] = newer
    second["metadata"]["cover_resolution"]["url"] = newer
    second["_exact_loan_cover_proofs"][0]["url"] = newer
    snapshot_method()(obj, [second])
    assert obj._loan_cover_decision_revision > initial_rev
    assert views[-1] == newer
    assert cache.peek(scope, revision=initial_rev) is None
    assert cache.peek(scope, revision=obj._loan_cover_decision_revision).uri == newer
    assert initial.uri != newer
    assert loan.thumbnail_url is None


def test_account_switch_and_old_library_snapshot_never_replay_foreign_image():
    obj, loan, views = host()
    snapshot_method()(obj, [a_row(loan)])
    displayed = ui_method()(obj, loan)
    assert displayed.thumbnail_url
    obj.service.current_identity.identity_id = "bob"
    obj.service._account_context_epoch += 1
    assert ui_method()(obj, loan).thumbnail_url is None
    obj.service.current_identity.identity_id = "alice"
    # Even ABA to the same named identity fails closed until the exact
    # refreshed epoch is delivered by Core.
    assert ui_method()(obj, loan).thumbnail_url is None


def test_other_borrower_or_credential_does_not_reuse_cached_proof():
    obj, loan, _views = host()
    snapshot_method()(obj, [a_row(loan)])
    for foreign in (
        a_loan(account="foreign"),
        a_loan(identity="bob"),
        a_loan(),
    ):
        if foreign.identity_id == loan.identity_id and foreign.source_account_id == loan.source_account_id:
            # A fresh DTO of the exact loan is allowed to reuse the cache.
            assert ui_method()(obj, foreign).thumbnail_url
        else:
            assert ui_method()(obj, foreign).thumbnail_url is None


def test_foreign_library_image_without_exact_loan_proof_cannot_be_assigned():
    obj, loan, views = host()
    foreign = a_row(a_loan(account="other"))
    snapshot_method()(obj, [foreign])
    assert views[-1] is None
    decision = obj._loan_cover_decision_cache.peek(
        exact_loan_cover_scope(loan),
        revision=obj._loan_cover_decision_revision,
    )
    assert decision.status == "missing"
    assert decision.uri == ""


def test_no_authoritative_library_rows_never_memoizes_missing_cover():
    obj, loan, views = host()
    snapshot_method()(obj, [])
    assert views[-1] is None
    assert not hasattr(obj, "_loan_cover_decision_cache")


def test_direct_bridge_requires_current_exact_materialized_core_snapshot():
    obj, loan, _ = host()
    row = a_row(loan)
    obj._loan_cover_decision_revision = 1
    other = (row,)
    assert bridge.loan_cover_decision(obj, loan, other) is None
    obj._loan_cover_projection = (
        obj.service._account_context_epoch, "alice", other,
    )
    assert bridge.loan_cover_decision(obj, loan, other).status == "selected"
    obj.service._account_context_epoch += 1
    assert bridge.loan_cover_decision(obj, loan, other) is None


def test_shared_scope_is_exact_and_refuses_unknown_account():
    known = a_loan()
    scope = exact_loan_cover_scope(known)
    assert scope is not None
    assert scope.identity_id == "alice"
    assert scope.credential_id == "card-a"
    assert scope.account_id == "account-a"
    assert scope.loan_id == "loan-1"
    assert exact_loan_cover_scope(a_loan(account=None)) is None


def test_supplier_thumbnail_is_preserved_against_weak_catalog_proof():
    obj, loan, _ = host()
    original = "https://covers.example.org/provider.jpg"
    loan.thumbnail_url = original
    snapshot_method()(obj, [a_row(a_loan(account="other"))])
    assert ui_method()(obj, loan).thumbnail_url == original
    assert loan.thumbnail_url == original


def test_new_loan_inventory_advances_cover_decision_epoch():
    fn = next(
        node for node in ast.parse(SOURCE.read_text(encoding="utf-8")).body
        if isinstance(node, ast.FunctionDef) and node.name == "_populate_loans"
    )
    namespace = {
        "Loan": Loan, "unambiguous_loan_rows": unambiguous_loan_rows,
        "loan_source_key": loan_source_key,
    }
    exec(compile(ast.fix_missing_locations(
        ast.Module(body=[fn], type_ignores=[]),
    ), str(SOURCE), "exec"), namespace)
    current = a_loan()
    owner = NS(
        service=NS(current_identity=NS(identity_id="alice"), last_loans=[]),
        _render_loans=lambda: None, _render_library=lambda: None,
    )
    namespace["_populate_loans"](owner, [current])
    assert owner._loan_cover_decision_revision == 1
    namespace["_populate_loans"](owner, [current])
    assert owner._loan_cover_decision_revision == 2
