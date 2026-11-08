"""P0 #1131 / #976: UI loan selection must retain exact Paris provenance."""
from __future__ import annotations

import ast
from copy import copy
from dataclasses import replace
from pathlib import Path

from cifereca.models import Loan
from cifereca.core.loan_cover_fallback import loan_local_cover
from cifereca.core.loan_source_identity import (
    loan_source_key, unique_library_loan, unambiguous_loan_rows,
)


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



def test_library_exact_empty_primary_account_does_not_accept_unknown_borrower():
    """None is unknown; it must not match an explicitly empty account ID."""
    unknown = _loan(account=None)
    primary = _loan(account="")
    selection = {
        "loan_id": primary.id,
        "provider_id": "paris",
        "source_credential": "paris-a",
        "source_account_id": "",
    }
    assert unique_library_loan([unknown], selection) is None
    assert unique_library_loan([unknown, primary], selection) is primary
    assert unique_library_loan([primary, unknown], selection) is primary
    assert unique_library_loan([primary], selection) is primary


def test_library_account_scope_rejects_malformed_type_without_string_coercion():
    """A malformed upstream borrower code never selects an exact loan."""
    primary = _loan(account="123")
    for supplied in (123, True, ["123"], {"code": "123"}):
        item = {"loan_id": primary.id, "source_account_id": supplied}
        assert unique_library_loan([primary], item) is None
    assert unique_library_loan(
        [primary], {"loan_id": primary.id, "source_account_id": "123"},
    ) is primary


def test_unknown_borrower_keeps_legacy_only_when_not_explicitly_qualified():
    unknown = _loan(account=None)
    # The historical single-loan display remains available without scope.
    assert unique_library_loan([unknown], {"loan_id": unknown.id}) is unknown
    # A request bearing the explicit empty account is NOT unqualified.
    assert unique_library_loan(
        [unknown], {"loan_id": unknown.id, "source_account_id": ""},
    ) is None



def test_same_upstream_id_different_backend_or_medium_never_aliases_gtk_rows():
    """The same borrower may have a physical and digital copy with one ID."""
    original = _loan()
    variants = (
        original,
        replace(original, provider="DILICOM"),
        replace(original, medium="physical"),
        replace(original, provider="DILICOM", medium="physical"),
    )
    keys = [loan_source_key(row) for row in variants]
    assert len(set(keys)) == len(variants)
    assert all(key.startswith("loan-source:") and len(key) == 76 for key in keys)
    # Descriptive refreshes do not change the transient source identity.
    assert loan_source_key(original) == loan_source_key(
        replace(original, title="Titre corrigé", due_at="2026-12-05"),
    )


def _run_refresh_without_gtk(host, rows):
    """Run the real UI refresh handler without importing GI or changing GTK."""
    from types import SimpleNamespace

    syntax = ast.parse(LOANS.read_text(encoding="utf-8"))
    handler = next(
        node for node in syntax.body
        if isinstance(node, ast.FunctionDef) and node.name == "_loans_refreshed"
    )
    display_helper = next(
        node for node in syntax.body
        if isinstance(node, ast.FunctionDef) and node.name == "_loan_display_cover"
    )
    compiled = compile(
        ast.fix_missing_locations(ast.Module(body=[display_helper, handler], type_ignores=[])),
        str(LOANS), "exec",
    )
    namespace = {"Loan": Loan, "loan_source_key": loan_source_key, "loan_local_cover": loan_local_cover, "copy": copy}
    exec(compiled, namespace)
    return namespace["_loans_refreshed"](host, rows)


def _refresh_host(selected):
    from types import SimpleNamespace

    events = []
    service = SimpleNamespace(last_loans=[])

    def populate(rows):
        # Same scope filtering as the real _populate_loans path.
        service.last_loans = [
            row for row in rows
            if type(row) is Loan and row.identity_id == "alice"
        ]

    host = SimpleNamespace(
        service=service,
        _displayed_loan=selected,
        _populate_loans=populate,
        loan_page=SimpleNamespace(detail=SimpleNamespace(
            show_loan=lambda row: events.append(("show", row)),
            empty=lambda *_args: events.append(("empty", None)),
        )),
        toast=lambda *_args: None,
    )
    return host, events


def test_refreshed_loan_detail_uses_exact_backend_medium_record_scope():
    selected = _loan()
    host, events = _refresh_host(selected)
    refreshed = replace(selected, title="Updated exact loan")
    rows = [
        replace(selected, provider="DILICOM"),
        replace(selected, medium="physical"),
        replace(selected, record_id="other-edition"),
        replace(selected, identity_id="bob"),
        refreshed,
    ]
    _run_refresh_without_gtk(host, rows)
    assert host._displayed_loan is refreshed
    assert events == [("show", refreshed)]


def test_refresh_never_selects_first_cross_backend_match():
    selected = _loan()
    host, events = _refresh_host(selected)
    rows = [
        replace(selected, provider="DILICOM"),
        replace(selected, medium="physical"),
        replace(selected, record_id="different-edition"),
    ]
    _run_refresh_without_gtk(host, rows)
    assert host._displayed_loan is None
    assert events == [("empty", None)]


def test_refresh_duplicate_exact_source_is_ambiguous_not_first_wins():
    selected = _loan()
    host, events = _refresh_host(selected)
    _run_refresh_without_gtk(
        host, [replace(selected, title="One"), replace(selected, title="Two")],
    )
    assert host._displayed_loan is None
    assert events == [("empty", None)]



def test_identical_provider_replays_coalesce_to_one_source_action_row():
    """Same exact business card may be replayed from two private snapshots."""
    loan = _loan()
    sibling = replace(loan, provider="DILICOM")
    unique = unambiguous_loan_rows([loan, replace(loan), sibling])
    assert unique == (loan, sibling)


def test_conflicting_exact_source_cards_are_suppressed_even_if_replayed_later():
    """No arbitrary last-wins action when a source ID carries two payloads."""
    loan = _loan()
    alternate = replace(loan, due_at="2026-12-31T12:00:00Z")
    sibling = replace(loan, source_credential="paris-b")
    assert unambiguous_loan_rows([
        loan, alternate, sibling, loan,
    ]) == (sibling,)
    assert unambiguous_loan_rows([sibling, loan, alternate]) == (sibling,)


def test_gtk_population_keeps_only_safe_source_keys_without_importing_gtk():
    from types import SimpleNamespace as NS

    syntax = ast.parse(LOANS.read_text(encoding="utf-8"))
    method = next(
        fn for fn in syntax.body if isinstance(fn, ast.FunctionDef)
        and fn.name == "_populate_loans"
    )
    namespace = {
        "Loan": Loan,
        "loan_source_key": loan_source_key,
        "unambiguous_loan_rows": unambiguous_loan_rows,
    }
    exec(compile(
        ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[])),
        str(LOANS), "exec",
    ), namespace)
    owner = _loan()
    conflicting = replace(owner, title="Inconsistent same source")
    sibling = replace(owner, provider="DILICOM")
    foreign = replace(owner, identity_id="bob")
    events = []
    host = NS(
        service=NS(current_identity=NS(identity_id="alice"), last_loans=[]),
        _render_loans=lambda: events.append("render"),
        _render_kobo_loans=lambda: events.append("kobo"),
    )
    namespace["_populate_loans"](host, [
        owner, conflicting, foreign, sibling, owner,
    ])
    assert host.service.last_loans == [sibling]
    assert host.loan_objects == {loan_source_key(sibling): sibling}
    assert events == ["render", "kobo"]


def test_library_loan_lookup_rejects_forged_nonloan_objects_and_fake_scalar_ids():
    from types import SimpleNamespace

    exact = _loan()
    forged = SimpleNamespace(id=exact.id, source_provider=exact.source_provider,
                             source_credential=exact.source_credential,
                             source_account_id=exact.source_account_id)
    item = {"loan_id": exact.id}
    assert unique_library_loan([forged], item) is None
    assert unique_library_loan([forged, exact], item) is exact
    for bad in (True, 123, ["same-paris-loan"], object(), ""):
        assert unique_library_loan([exact], {"loan_id": bad}) is None
    assert unique_library_loan([exact], None) is None


def test_library_loan_lookup_does_not_coerce_private_scope_values():
    exact = _loan()
    for scope in ("provider_id", "identity_id", "item_id", "source_credential"):
        for wrong in (True, 12, object(), ["paris"]):
            assert unique_library_loan(
                [exact], {"loan_id": exact.id, scope: wrong},
            ) is None
    assert unique_library_loan([exact], {
        "loan_id": exact.id, "provider_id": "paris",
        "identity_id": "alice", "item_id": "book-123",
        "source_credential": "paris-a",
    }) is exact


def test_loan_lookup_refuses_partial_or_oversized_inventory_without_action():
    exact = _loan()

    def interrupted():
        yield exact
        raise RuntimeError("/private/other-account")

    assert unique_library_loan(interrupted(), {"loan_id": exact.id}) is None
    seen = []

    def oversized():
        for index in range(10002):
            seen.append(index)
            yield exact if index == 0 else _loan(credential=f"card-{index}")

    assert unique_library_loan(oversized(), {"loan_id": exact.id}) is None
    assert len(seen) == 10001
    assert unique_library_loan([exact], {"loan_id": exact.id}) is exact


def test_actionable_loan_rows_are_never_published_from_partial_inventory():
    exact = _loan()

    def interrupted():
        yield exact
        raise RuntimeError("/private/lender/credential")

    assert unambiguous_loan_rows(interrupted()) == ()
    seen = []

    def excessive():
        for index in range(10002):
            seen.append(index)
            yield exact if index == 0 else _loan(credential=f"other-{index}")

    assert unambiguous_loan_rows(excessive()) == ()
    assert len(seen) == 10001
    assert unambiguous_loan_rows([exact, replace(exact)]) == (exact,)
