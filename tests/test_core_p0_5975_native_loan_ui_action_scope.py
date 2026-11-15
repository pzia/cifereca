"""P0 #5975/#5851: a GTK loan action never stringifies untrusted source coordinates."""
from __future__ import annotations

import ast
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from cifereca.models import Loan
from cifereca.core.loan_source_identity import (
    loan_source_key, unambiguous_loan_rows, unique_library_loan,
)


LOANS_CONTROLLER = (
    Path(__file__).resolve().parents[1]
    / "src/cifereca/gui/loans_controller.py"
)


def _loan() -> Loan:
    return Loan(
        id="loan-123", provider="PNB", medium="digital",
        record_id="edition-42", title="A title",
        source_provider="paris", source_credential="credential-a",
        source_account_id="borrower-a", identity_id="alice",
    )


class _HostileCoordinate:
    def __str__(self):
        raise AssertionError("provider coordinate was stringified")

    def __bool__(self):
        raise AssertionError("provider coordinate was coerced to bool")

    def __eq__(self, _other):
        raise AssertionError("provider coordinate comparison ran")


class _HostileString(str):
    def __eq__(self, _other):
        raise AssertionError("str subclass comparison ran")

    def __str__(self):
        raise AssertionError("str subclass coercion ran")


@pytest.mark.parametrize(("field", "bad"), [
    ("id", _HostileCoordinate()),
    ("id", _HostileString("loan-123")),
    ("source_provider", _HostileCoordinate()),
    ("source_provider", _HostileString("paris")),
    ("source_credential", _HostileCoordinate()),
    ("source_credential", _HostileString("credential-a")),
    ("source_account_id", _HostileCoordinate()),
    ("source_account_id", _HostileString("borrower-a")),
    ("record_id", _HostileCoordinate()),
    ("record_id", _HostileString("edition-42")),
    ("provider", _HostileCoordinate()),
    ("medium", _HostileCoordinate()),
    ("identity_id", _HostileCoordinate()),
    ("identity_id", _HostileString("alice")),
    ("source_credential", ""),
    ("source_provider", ""),
    ("id", ""),
])
def test_invalid_native_coordinates_cannot_create_actions_or_mask_valid_loan(
    field, bad,
):
    good = _loan()
    bad_loan = replace(good, **{field: bad})
    # A bad candidate is not allowed into a SourceKey dict. A valid sibling
    # remains visible instead of being discarded with the entire inventory.
    assert unambiguous_loan_rows([bad_loan, good]) == (good,)
    assert unambiguous_loan_rows([good, bad_loan]) == (good,)
    assert unique_library_loan([bad_loan], {"loan_id": good.id}) is None
    assert unique_library_loan([bad_loan, good], {"loan_id": good.id}) is good


def test_exact_optional_borrower_and_edition_keep_native_source_semantics():
    good = _loan()
    unknown = replace(good, source_account_id=None, record_id=None)
    empty_primary = replace(good, source_account_id="")
    assert len(unambiguous_loan_rows([good, unknown, empty_primary])) == 3
    assert len({
        loan_source_key(good),
        loan_source_key(unknown),
        loan_source_key(empty_primary),
    }) == 3
    assert unique_library_loan(
        [unknown], {"loan_id": unknown.id},
    ) is unknown
    assert unique_library_loan(
        [unknown], {"loan_id": unknown.id, "source_account_id": ""},
    ) is None


def test_gtk_population_does_not_compare_hostile_identity_before_validation():
    # Compile the production controller function without importing GTK.
    tree = ast.parse(LOANS_CONTROLLER.read_text(encoding="utf-8"))
    function = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_populate_loans"
    )
    namespace = {
        "Loan": Loan, "loan_source_key": loan_source_key,
        "unambiguous_loan_rows": unambiguous_loan_rows,
    }
    exec(compile(
        ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[])),
        str(LOANS_CONTROLLER), "exec",
    ), namespace)
    events = []
    service = SimpleNamespace(
        current_identity=SimpleNamespace(identity_id="alice"),
        last_loans=[],
    )
    owner = SimpleNamespace(
        service=service,
        _render_loans=lambda: events.append("loans"),
        _render_library=lambda: events.append("library"),
    )
    good = _loan()
    # A plain Loan dataclass may still carry a provider's arbitrary object
    # despite Python field annotations.
    hostile_identity = replace(good, identity_id=_HostileCoordinate())
    namespace["_populate_loans"](owner, [hostile_identity, good])
    assert service.last_loans == [good]
    assert owner.loan_objects == {loan_source_key(good): good}
    assert events == ["loans", "library"]

    # An invalid current selector cannot authorize a foreign row.
    service.current_identity.identity_id = _HostileCoordinate()
    namespace["_populate_loans"](owner, [good])
    assert service.last_loans == []
    assert owner.loan_objects == {}
