"""P1 #3121: opt-in startup acquisition; no provider ID-based exemption.

No network, ACSM, ADEPT, return or mutation is executed by these tests.
"""
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from cifereca.models import Account, Loan
from cifereca.providers.base import ProviderCapabilities
from cifereca.core.sync_coordination import _opening_loan_acquisition_allowed


def account(provider="paris", credential="card-a", *, borrower="", identity="alice",
            primary=True):
    return Account(
        provider_id=provider, credential_id=credential,
        unique_id=borrower, identity_id=identity, is_primary=primary,
    )


def loan(a, *, credential=None, provider=None, borrower=None, identity=None,
         medium="digital", has_url=True, raw=None):
    return Loan(
        id="synthetic", provider="DILICOM", medium=medium,
        record_id="synthetic-record", title="Synthetic novel",
        source_provider=provider if provider is not None else a.provider_id,
        source_credential=credential if credential is not None else a.credential_id,
        source_account_id=borrower if borrower is not None else a.unique_id,
        identity_id=identity if identity is not None else a.identity_id,
        ebook_url="https://example.invalid/acsm" if has_url else None,
        raw={"synthetic": True} if raw is None else raw,
    )


def session(a, *, opt_in=False, connected=True, loans=True, digital_download=True,
            accounts=None):
    return NS(
        provider_id=a.provider_id, credential_id=a.credential_id,
        connected=connected,
        capabilities=ProviderCapabilities(
            loans=loans, digital_download=digital_download,
            opening_loan_auto_acquisition=opt_in,
        ),
        accounts=list(accounts if accounts is not None else [a]),
    )


def test_paris_historical_path_is_explicit_opt_in_not_name_based():
    a = account()
    row = loan(a)
    s = session(a, opt_in=True)
    assert _opening_loan_acquisition_allowed(row, session=s)
    assert not _opening_loan_acquisition_allowed(row, session=session(a))
    # A completely unrelated plugin with the same data cannot auto-acquire
    # merely because it supplies a digital URL or raw metadata.
    other = account("future-library")
    assert not _opening_loan_acquisition_allowed(loan(other),
                                                  session=session(other))


@pytest.mark.parametrize("provider", ["bnf-pret", "kobo", "numilog",
                                      "future-library", "paris"])
def test_default_false_is_the_universal_new_provider_policy(provider):
    a = account(provider)
    for evidence in (loan(a), loan(a, has_url=False, raw={"book": True})):
        assert not _opening_loan_acquisition_allowed(
            evidence, session=session(a, opt_in=False)
        )


@pytest.mark.parametrize("change", [
    "closed", "missing_loans_capability", "missing_digital_capability",
    "foreign_provider", "foreign_credential", "foreign_identity",
    "foreign_borrower", "no_accounts", "stale_alias",
])
def test_startup_admission_refuses_session_scope_or_capability_conflict(change):
    a = account()
    row = loan(a)
    s = session(a, opt_in=True)
    if change == "closed":
        s.connected = False
    elif change == "missing_loans_capability":
        s.capabilities = replace(s.capabilities, loans=False)
    elif change == "missing_digital_capability":
        s.capabilities = replace(s.capabilities, digital_download=False)
    elif change == "foreign_provider":
        s.provider_id = "another-plugin"
    elif change == "foreign_credential":
        s.credential_id = "other-card"
    elif change == "foreign_identity":
        s.accounts = [account(identity="bob")]
    elif change == "foreign_borrower":
        s.accounts = [account(borrower="linked", primary=False)]
    elif change == "no_accounts":
        s.accounts = []
    else:
        row.source_account_id = "unknown-alias"
    assert not _opening_loan_acquisition_allowed(row, session=s)


def test_paris_linked_reader_is_allowed_only_when_this_session_lists_it():
    main = account()
    linked = account(borrower="linked-2", primary=False)
    session_one = session(main, opt_in=True, accounts=[main, linked])
    assert _opening_loan_acquisition_allowed(loan(linked), session=session_one)
    assert not _opening_loan_acquisition_allowed(loan(linked),
                                                 session=session(main, opt_in=True))


def test_empty_primary_id_and_legacy_alias_are_not_wildcards():
    main = account()
    linked = account(borrower="linked-2", primary=False)
    s = session(main, opt_in=True, accounts=[main, linked])
    assert _opening_loan_acquisition_allowed(
        loan(main, borrower="primary"), session=s
    )
    assert not _opening_loan_acquisition_allowed(
        loan(linked, borrower="primary"), session=session(linked, opt_in=True)
    )


@pytest.mark.parametrize("medium,has_url,raw", [
    ("physical", True, {"synthetic": True}),
    ("unknown", True, {"synthetic": True}),
    ("digital", False, {}),
])
def test_no_technical_download_trigger_in_unsupported_medium_or_empty_evidence(
    medium, has_url, raw,
):
    a = account()
    assert not _opening_loan_acquisition_allowed(
        loan(a, medium=medium, has_url=has_url, raw=raw),
        session=session(a, opt_in=True),
    )


def test_no_account_service_or_plugin_io_is_required():
    a = account()
    s = session(a, opt_in=True)
    s.accounts = property(lambda _: pytest.fail("should only inspect current rows"))
    assert not _opening_loan_acquisition_allowed(object(), session=s)
    assert not _opening_loan_acquisition_allowed(loan(a), session=None)


def test_declared_bnf_get_staging_does_not_become_startup_acquisition():
    bnf = account("bnf-pret", "bnf-card", borrower="reader")
    # A BnF internal ACSM GET does NOT advertise opening acquisition.
    s = session(bnf, opt_in=False, digital_download=False)
    assert not _opening_loan_acquisition_allowed(loan(bnf), session=s)
    # Even a misdeclared startup opt-in cannot override the missing download
    # capability and the actual manual/double-confirmation BnF gate.
    s.capabilities = replace(s.capabilities, opening_loan_auto_acquisition=True)
    assert not _opening_loan_acquisition_allowed(loan(bnf), session=s)
