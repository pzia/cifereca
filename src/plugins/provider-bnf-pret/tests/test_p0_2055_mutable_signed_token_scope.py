"""P0 #2055: mutable Core borrower/loan never authorizes a stale signed GET.

Synthetic HTTPX fixture only: no live BnF, DRM, acquisition or file publication.
"""
from __future__ import annotations

import pytest

from bnf_token_fixture import connected
from cifereca.plugin_api import ProviderError


@pytest.mark.parametrize("target,attribute,new_value", [
    ("loan", "id", "loan-b"),
    ("loan", "record_id", "book-b"),
    ("loan", "provider", "PARIS"),
    ("loan", "medium", "physical"),
    ("loan", "state", "returned"),
    ("loan", "source_provider", "paris"),
    ("loan", "source_credential", "credential-b"),
    ("loan", "source_account_id", "account-b"),
    ("loan", "identity_id", "profile-b"),
    ("account", "provider_id", "paris"),
    ("account", "credential_id", "credential-b"),
    ("account", "unique_id", "account-b"),
    ("account", "identity_id", "profile-b"),
])
def test_changed_scope_during_activity_blocks_signed_get(
    tmp_path, target, attribute, new_value,
):
    session, account, loan, _, _, downloaded = connected()
    original = session.client.activity

    def activity():
        page = original()
        setattr(loan if target == "loan" else account, attribute, new_value)
        return page

    session.client.activity = activity
    try:
        with pytest.raises(ProviderError, match="^loan_not_observed$"):
            session.download_digital(
                loan, account, tmp_path, explicit_user_action=True,
            )
        assert downloaded == []
        assert not list(tmp_path.rglob("*.acsm"))
    finally:
        session.close()


def test_scope_change_after_signed_action_parse_blocks_get(tmp_path, monkeypatch):
    session, account, loan, _, _, downloaded = connected()
    namespace = session.download_digital.__func__.__globals__
    original = namespace["signed_acsm_action"]

    def parse_and_rebind(*args):
        url = original(*args)
        account.identity_id = "new-identity"
        return url

    monkeypatch.setitem(namespace, "signed_acsm_action", parse_and_rebind)
    try:
        with pytest.raises(ProviderError, match="^loan_not_observed$"):
            session.download_digital(
                loan, account, tmp_path, explicit_user_action=True,
            )
        assert downloaded == []
        assert not list(tmp_path.rglob("*.acsm"))
    finally:
        session.close()


@pytest.mark.parametrize("target,attribute,new_value", [
    ("loan", "id", "loan-b"),
    ("loan", "record_id", "book-b"),
    ("loan", "state", "returned"),
    ("loan", "identity_id", "profile-b"),
    ("account", "unique_id", "account-b"),
    ("account", "identity_id", "profile-b"),
])
def test_scope_change_during_token_get_prevents_staging(
    tmp_path, target, attribute, new_value,
):
    session, account, loan, _, _, downloaded = connected()
    original = session.client.loan_token

    def token_and_rebind(*args):
        token = original(*args)
        setattr(loan if target == "loan" else account, attribute, new_value)
        return token

    session.client.loan_token = token_and_rebind
    try:
        with pytest.raises(ProviderError, match="^loan_not_observed$"):
            session.download_digital(
                loan, account, tmp_path, explicit_user_action=True,
            )
        assert len(downloaded) == 1  # Already in flight; no new GET.
        assert not list(tmp_path.rglob("*.acsm"))
    finally:
        session.close()


def test_scope_change_while_saving_discards_private_candidate(tmp_path, monkeypatch):
    session, account, loan, _, _, downloaded = connected()
    namespace = session.download_digital.__func__.__globals__
    original = namespace["save_private_loan_token"]

    def save_and_revoke(*args):
        path = original(*args)
        loan.state = "returned"
        return path

    monkeypatch.setitem(namespace, "save_private_loan_token", save_and_revoke)
    try:
        with pytest.raises(ProviderError, match="^loan_not_observed$"):
            session.download_digital(
                loan, account, tmp_path, explicit_user_action=True,
            )
        assert len(downloaded) == 1
        assert not list(tmp_path.rglob("*.acsm"))
        assert not list(tmp_path.rglob("*.part"))
    finally:
        session.close()


def test_unchanged_scope_still_allows_single_explicit_get(tmp_path):
    session, account, loan, _, _, downloaded = connected()
    try:
        candidate, url = session.download_digital(
            loan, account, tmp_path, explicit_user_action=True,
        )
        assert candidate.exists() and candidate.suffix == ".acsm"
        assert url == "" and len(downloaded) == 1
    finally:
        session.close()
