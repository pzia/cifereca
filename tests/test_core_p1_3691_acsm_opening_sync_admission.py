"""The existing opening-sync Core gate admits only observed BiblioAccess offers.

Tests the real generic Core contract, not a duplicated provider-specific gate.
"""
from types import SimpleNamespace as NS
from dataclasses import replace

from cifereca.core.sync_coordination import _opening_loan_acquisition_allowed
from cifereca.models import Account, Loan


def _source():
    account = Account(
        display_name="Synthetic La Rochelle", unique_id="primary",
        is_primary=True, provider_id="biblioaccess.la-rochelle",
        credential_id="synthetic-credential", identity_id="synthetic-identity",
    )
    loan = Loan(
        id="biblioaccess:1409:10001",
        provider="BIBLIOACCESS", medium="digital",
        record_id="20002", title="Synthetic Book",
        source_provider="biblioaccess.la-rochelle",
        source_credential="synthetic-credential",
        source_account_id="primary", identity_id="synthetic-identity",
        raw={"acsm_offer_present": True},
    )
    caps = NS(loans=True, digital_download=True,
              opening_loan_auto_acquisition=True)
    session = NS(
        provider_id="biblioaccess.la-rochelle",
        credential_id="synthetic-credential", connected=True,
        accounts=[account], capabilities=caps,
    )
    return account, loan, session


def test_core_opening_sync_admits_same_credential_and_positive_acsm_offer():
    account, loan, session = _source()
    assert _opening_loan_acquisition_allowed(loan, session=session)
    assert not _opening_loan_acquisition_allowed(
        replace(loan, raw={}), session=session)
    assert not _opening_loan_acquisition_allowed(
        replace(loan, source_credential="other"), session=session)
    assert not _opening_loan_acquisition_allowed(
        replace(loan, source_account_id="other"), session=session)
    assert not _opening_loan_acquisition_allowed(
        replace(loan, identity_id="other"), session=session)
    session.connected = False
    assert not _opening_loan_acquisition_allowed(loan, session=session)


def test_core_opening_sync_does_not_acquire_when_download_capability_missing():
    _, loan, session = _source()
    session.capabilities.digital_download = False
    assert not _opening_loan_acquisition_allowed(loan, session=session)
