"""P0 #5735: installed existing-loan alias is a scope check, never permission.

Synthetic Paris and BnF sessions only; no HTTP, loan mutation or ADEPT.
"""
from dataclasses import replace

import pytest

from cifereca.core.loan_action_scope import capture_loan_action_scope
from tests.test_core_p1_3121_paris_renewal_one_post import setup as paris_setup, account as paris_account, loan as paris_loan
from tests.test_core_p0_2055_bnf_explicit_stage import connected_core


def _capture(service, session, account, loan):
    return capture_loan_action_scope(
        service, loan, account, session,
        expected_identity_id=account.identity_id,
    )


def test_existing_paris_primary_alias_requires_explicit_descriptor(tmp_path):
    service, _account, _loan, session = paris_setup(tmp_path)
    try:
        primary = paris_account(borrower="")
        session._accounts = [primary]
        service.accounts = [primary]
        observed = paris_loan(primary)
        # This is an existing-loan scope, not an authorization to renew.
        service._account_for_loan = lambda _row: primary
        lease = _capture(service, session, primary, observed)
        assert lease is not None and lease.current(service)
        original = session.descriptor
        session.descriptor = replace(original, existing_primary_loan_account_alias=None)
        assert not lease.current(service)
        assert _capture(service, session, primary, observed) is None
    finally:
        service.close()


@pytest.mark.parametrize("alias", ["", "bad/path", "a space", 4, True, "a" * 65])
def test_invalid_existing_loan_alias_fails_closed(tmp_path, alias):
    service, account, observed, session = paris_setup(tmp_path)
    try:
        session.descriptor = replace(
            session.descriptor, existing_primary_loan_account_alias=alias,
        )
        assert _capture(service, session, account, observed) is None
        assert session.posts == session.reads == []
    finally:
        service.close()


def test_alias_never_applies_to_a_linked_nonprimary_borrower(tmp_path):
    service, account, observed, session = paris_setup(tmp_path)
    try:
        assert account.unique_id == "reader" and not account.is_primary
        assert _capture(service, session, account, observed) is not None
        mismatched = replace(observed, source_account_id="primary")
        assert _capture(service, session, account, mismatched) is None
        assert session.posts == session.reads == []
    finally:
        service.close()


def test_existing_bnf_scope_requires_exact_account_even_with_foreign_alias(connected_core):
    service, session, account, observed, _state, downloaded = connected_core
    assert _capture(service, session, account, observed) is not None
    # A new policy object, even with the same source ID, revokes the old lease.
    old = _capture(service, session, account, observed)
    session.descriptor = replace(
        session.descriptor, existing_primary_loan_account_alias="primary",
    )
    assert not old.current(service)
    assert _capture(service, session, account, observed) is not None
    assert downloaded == []
