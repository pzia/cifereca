"""#1120: traces de prêts fail-closed, sans appel aux méthodes des plugins."""
from __future__ import annotations

from types import SimpleNamespace as NS

from cifereca.core.loan_safe_trace import loan_trace_ref, unbound_loan_trace_ref


PRIVATE = "https://private.invalid/borrow.acsm?token=DO_NOT_EXPOSE"


class UntrustedStr(str):
    def __str__(self):
        raise AssertionError(PRIVATE)

    def __getitem__(self, _slice):
        raise AssertionError(PRIVATE)

    def encode(self, *_a, **_kw):
        raise AssertionError(PRIVATE)


class UntrustedLoan:
    def __getattribute__(self, name):
        if name in {"source_provider", "source_account_id"}:
            raise RuntimeError(PRIVATE)
        if name == "id":
            return UntrustedStr(PRIVATE)
        if name == "source_credential":
            return "safe-credential"
        if name == "record_id":
            return "safe-record"
        raise AttributeError(name)


def test_trace_ignores_raising_getters_and_non_native_strings_without_leaking():
    safe = NS(source_credential="safe-credential", record_id="safe-record")
    expected = loan_trace_ref(safe, "identity-1")
    actual = loan_trace_ref(UntrustedLoan(), "identity-1")
    assert actual == expected
    assert len(actual) == 20
    assert PRIVATE not in actual
    assert actual != loan_trace_ref(safe, "identity-2")


def test_identity_subclass_never_executes_plugin_code():
    loan = NS(id="stable-loan")
    assert loan_trace_ref(loan, UntrustedStr(PRIVATE)) == loan_trace_ref(loan, None)
    assert loan_trace_ref(loan, "identity-1") != loan_trace_ref(loan, None)


def test_unbound_compatibility_and_string_coordinates_preserved():
    loan = NS(
        source_provider="paris", source_credential="primary",
        source_account_id="account", id="loan", record_id="book",
    )
    assert loan_trace_ref(loan, "identity") == loan_trace_ref(loan, "identity")
    assert loan_trace_ref(loan, "identity") != loan_trace_ref(loan, None)
    assert unbound_loan_trace_ref("loan") == loan_trace_ref(NS(id="loan"), None)
