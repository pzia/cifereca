"""P0 #6366: only current action-owned loans may produce list/N2 rows."""
from __future__ import annotations

import ast
from pathlib import Path

from cifereca.models import Loan
from cifereca.core.loan_live_projection import current_actionable_loan_rows
from cifereca.core.loan_source_identity import loan_source_key


def loan(*, credential="a", identity="alice", url=None):
    return Loan(
        id="loan-7", provider="paris", medium="digital",
        record_id="record-7", title="Homonyme",
        source_provider="paris", source_credential=credential,
        source_account_id="", identity_id=identity, thumbnail_url=url,
    )


def test_current_action_inventory_accepts_only_original_dto():
    a = loan(url="https://example.org/a")
    objects = {loan_source_key(a): a}
    assert current_actionable_loan_rows([a], objects, identity_id="alice") == (a,)
    assert current_actionable_loan_rows([a, a], objects, identity_id="alice") == (a,)
    replay = loan(url="https://example.org/a")
    assert replay == a and replay is not a
    assert current_actionable_loan_rows(
        [replay], objects, identity_id="alice",
    ) == ()


def test_stale_account_and_provider_replay_cannot_rebind_n2():
    alice = loan(credential="a", identity="alice")
    bob = loan(credential="b", identity="bob")
    objects = {loan_source_key(alice): alice}
    assert current_actionable_loan_rows(
        [alice, bob], objects, identity_id="alice",
    ) == (alice,)
    assert current_actionable_loan_rows(
        [alice, bob], objects, identity_id="bob",
    ) == ()
    assert current_actionable_loan_rows(
        [bob], {loan_source_key(bob): bob}, identity_id="bob",
    ) == (bob,)


def test_ambiguous_source_key_suppresses_both_even_if_map_has_one():
    a = loan(url="https://example.org/a")
    changed = loan(url="https://example.org/b")
    assert current_actionable_loan_rows(
        [a, changed], {loan_source_key(a): a}, identity_id="alice",
    ) == ()


def test_malformed_map_and_unbounded_inventory_fail_closed():
    a = loan()
    assert current_actionable_loan_rows([a], {}, identity_id="alice") == ()
    assert current_actionable_loan_rows([a], {loan_source_key(a): a}, identity_id=None) == ()
    assert current_actionable_loan_rows(
        (a for _ in range(10001)),
        {loan_source_key(a): a}, identity_id="alice",
    ) == ()


def test_gtk_render_calls_live_core_gate_before_cover_projection():
    path = Path(__file__).resolve().parents[1] / "src/cifereca/gui/loans_controller.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    method = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                  and n.name == "_render_loans")
    source = ast.unparse(method)
    assert "current_actionable_loan_rows(" in source
    assert source.index("current_actionable_loan_rows(") < source.index("loan_embedded_cover_for_host")
