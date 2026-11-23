"""#6002: read-only Home projections cannot double-count exact borrower scopes."""
from dataclasses import replace

from cifereca.plugin_api.digital_loan_availability import (
    DigitalLoanAvailability, digital_loan_availability_lines,
)


def _row(**kwargs):
    return DigitalLoanAvailability(
        identity_id="profile", provider_id="provider", credential_id="credential",
        borrower_id="borrower", provider_label="Provider",
        connection_label="Connection", borrower_label="Reader",
        observed_loans=1, **kwargs,
    )


def test_duplicate_exact_scope_fails_closed_without_leaking_coordinates():
    first = _row()
    second = replace(first, observed_loans=2)
    lines = digital_loan_availability_lines((first, second), identity_id="profile")
    assert lines == ("Reste inconnu — portée des comptes dupliquée.",)
    assert "credential" not in str(lines)
    assert "3 prêt" not in str(lines)


def test_distinct_credential_or_borrower_remains_separate():
    first = _row()
    for second in (
        replace(first, credential_id="credential-2"),
        replace(first, borrower_id="borrower-2"),
    ):
        lines = digital_loan_availability_lines((first, second), identity_id="profile")
        assert len(lines) == 3
        assert all("Reste inconnu" in line for line in lines[:2])
        assert lines[-1].startswith("Total non calculable")


def test_forged_unhashable_scope_fails_closed_before_duplicate_lookup():
    first = _row()
    for field, forged in (
        ("identity_id", ["profile"]),
        ("provider_id", ["provider"]),
        ("credential_id", ["credential"]),
        ("borrower_id", ["borrower"]),
        ("credential_id", {"secret": "do-not-print"}),
    ):
        altered = replace(first)
        object.__setattr__(altered, field, forged)
        lines = digital_loan_availability_lines((altered,), identity_id="profile")
        assert lines == ("Reste inconnu — projection des comptes invalide.",)
        assert "do-not-print" not in str(lines)
