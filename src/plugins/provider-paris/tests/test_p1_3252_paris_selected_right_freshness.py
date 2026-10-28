"""#3252 Paris: explicit selected right re-observation before PNB delivery.

Real Paris session and typed DILICOM inventory with synthetic transport.
No real Paris account, PNB token, ADEPT or Core A→B publication.
"""
from dataclasses import replace

import pytest

from cifereca.plugin_api import ProviderConfigurationError
from tests.test_p0_3252_paris_download_scope import SIGNED, prepare


def raw_loan(url=SIGNED, *, right="loan-1", edition="edition-1"):
    return {
        "Id": right, "RecordId": edition, "Title": "Fresh synthetic loan",
        "State": "loan",
        "AdditionalProperties": {"EbookUrl": url},
    }


def test_selected_fresh_loan_gets_only_current_url(tmp_path, monkeypatch):
    session, account, loan, client, content_gets = prepare(tmp_path, monkeypatch)
    fresh_url = SIGNED.replace("synthetic=1", "synthetic=2")
    client.digital = [raw_loan(fresh_url)]
    try:
        path, used_url = session.download_digital(
            loan, replace(account), tmp_path,
            expected_right_id="loan-1", expected_identity_id="identity-A",
        )
        # The signed PNB URL is used internally for the fresh GET only;
        # the selected-right return must not expose it as a public receipt.
        assert path.exists() and used_url == ""
        assert content_gets == [fresh_url]  # never the caller's stale URL
        assert client.calls == [
            ("DILICOM", "reader-A"), ("SYRACUSE", "reader-A"),
        ]
    finally:
        session.close()


@pytest.mark.parametrize("wrong", [
    {"expected_right_id": "another-loan", "expected_identity_id": "identity-A"},
    {"expected_right_id": "loan-1", "expected_identity_id": "identity-B"},
    {"expected_right_id": "loan-1"},
    {"expected_identity_id": "identity-A"},
])
def test_invalid_core_selected_right_blocks_before_any_private_get(
    tmp_path, monkeypatch, wrong,
):
    session, account, loan, client, content_gets = prepare(tmp_path, monkeypatch)
    client.digital = [raw_loan()]
    try:
        with pytest.raises(
            ProviderConfigurationError, match="paris_selected_right_not_admitted",
        ):
            session.download_digital(loan, account, tmp_path, **wrong)
        assert client.calls == [] and content_gets == []
        assert not list(tmp_path.iterdir())
    finally:
        session.close()


@pytest.mark.parametrize("digital", [
    [], [raw_loan(right="other-loan")],
    [raw_loan(edition="foreign-edition")],
    [raw_loan(), raw_loan()],
    [raw_loan(), raw_loan(url=SIGNED + "&copy=2")],
])
def test_missing_ambiguous_or_foreign_dilicom_right_never_gets_file(
    tmp_path, monkeypatch, digital,
):
    session, account, loan, client, content_gets = prepare(tmp_path, monkeypatch)
    client.digital = digital
    try:
        with pytest.raises(
            ProviderConfigurationError, match="paris_selected_right_unobserved",
        ):
            session.download_digital(
                loan, account, tmp_path, expected_right_id=loan.id,
                expected_identity_id=account.identity_id,
            )
        assert content_gets == []
        assert client.calls == [
            ("DILICOM", "reader-A"), ("SYRACUSE", "reader-A"),
        ]
        assert not list(tmp_path.iterdir())
    finally:
        session.close()


def test_selected_dilicom_failure_is_uncertainty_not_file_permission(tmp_path, monkeypatch):
    session, account, loan, client, content_gets = prepare(tmp_path, monkeypatch)
    def fail_loans(upstream, account_id):
        client.calls.append((upstream, account_id))
        if upstream == "DILICOM":
            raise RuntimeError("Synthetic DILICOM unavailable")
        return []
    client.loans = fail_loans
    try:
        with pytest.raises(
            ProviderConfigurationError, match="paris_selected_right_unobserved",
        ):
            session.download_digital(
                loan, account, tmp_path, expected_right_id=loan.id,
                expected_identity_id=account.identity_id,
            )
        assert content_gets == []
        assert client.calls == [
            ("DILICOM", "reader-A"), ("SYRACUSE", "reader-A"),
        ]
    finally:
        session.close()


def test_broker_rebind_after_first_inventory_get_blocks_second_and_token(
    tmp_path, monkeypatch,
):
    session, account, loan, client, content_gets = prepare(tmp_path, monkeypatch)
    client.digital = [raw_loan()]
    original_loans = client.loans
    def change_owner(upstream, account_id):
        result = original_loans(upstream, account_id)
        if upstream == "DILICOM":
            session.identity_broker.identities = ("identity-B",)
        return result
    client.loans = change_owner
    try:
        with pytest.raises(
            ProviderConfigurationError, match="account_scope_mismatch",
        ):
            session.download_digital(
                loan, account, tmp_path, expected_right_id=loan.id,
                expected_identity_id=account.identity_id,
            )
        assert client.calls == [("DILICOM", "reader-A")]
        assert content_gets == []
    finally:
        session.close()


def test_legacy_explicit_download_keeps_its_original_no_inventory_get(
    tmp_path, monkeypatch,
):
    session, account, loan, client, content_gets = prepare(tmp_path, monkeypatch)
    try:
        path, _ = session.download_digital(loan, account, tmp_path)
        assert path.exists()
        assert client.calls == []
        assert content_gets == [SIGNED]
    finally:
        session.close()


def test_selected_loan_without_fresh_ebook_link_never_posts_new_pnb_borrow(
    tmp_path, monkeypatch,
):
    session, account, loan, client, content_gets = prepare(tmp_path, monkeypatch)
    client.digital = [raw_loan(url="")]
    posted = []
    client.pnb_preferences = lambda: posted.append("PREFS")
    client.download_digital_document = lambda *_: posted.append("POST")
    try:
        with pytest.raises(
            ProviderConfigurationError, match="paris_selected_delivery_unobserved",
        ):
            session.download_digital(
                loan, account, tmp_path, expected_right_id=loan.id,
                expected_identity_id=account.identity_id,
            )
        assert posted == []  # no fallback quota/borrow POST
        assert content_gets == []
        assert not list(tmp_path.iterdir())
    finally:
        session.close()
