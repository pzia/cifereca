"""#3252 Paris: explicit selected right re-observation before PNB delivery.

Real Paris session and typed DILICOM inventory with synthetic transport.
No real Paris account, PNB token, ADEPT or Core A→B publication.
"""
from dataclasses import replace

import pytest

from cifereca.plugin_api import ProviderCapabilityError, ProviderConfigurationError
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

# 30/10 owner arbitration: file acquisition is independent from right status.

def test_pnb_link_qualifies_file_even_with_unknown_right(tmp_path, monkeypatch):
    session, account, _loan, client, gets = prepare(tmp_path, monkeypatch)
    client.digital = [raw_loan()]
    try:
        report = session.rights_observation_snapshot(account)
        right = report.exact("edition-1", "loan-1", "loan")
        assert right is not None and right.state == "unknown"
        assert right.delivery_attested
        assert right.delivery_offer_id.startswith("paris.pnb:")
        assert gets == []
        selected = session.rights_selected_delivery_offer_snapshot(
            account, edition_id="edition-1", right_id="loan-1",
            kind="loan", branch="dilicom", explicit_user_action=True,
        )
        assert selected.rights[0].delivery_attested
        assert selected.rights[0].state == "unknown"
        assert selected.rights[0].delivery_offer_id == right.delivery_offer_id
        assert gets == []
    finally:
        session.close()


def test_paris_candidate_gets_only_fresh_pnb_url_with_unknown_right(
    tmp_path, monkeypatch,
):
    session, account, _loan, client, gets = prepare(tmp_path, monkeypatch)
    client.digital = [raw_loan()]
    try:
        right = session.rights_observation_snapshot(account).rights[0]
        fresh_url = SIGNED.replace("synthetic=1", "synthetic=2")
        client.digital = [raw_loan(fresh_url)]
        path, receipt = session.rights_selected_delivery_candidate(
            account, edition_id="edition-1", right_id="loan-1",
            kind="loan", branch="dilicom",
            delivery_offer_id=right.delivery_offer_id,
            target_dir=tmp_path, explicit_user_action=True,
        )
        assert path.is_file() and path.read_bytes().startswith(b"%PDF-")
        assert receipt == "" and gets == [fresh_url]
    finally:
        session.close()


@pytest.mark.parametrize("link", [
    "", "javascript:alert(1)", "file:///private/loan",
    "//foreign.example/item", "https://user:password@example.com/secret",
    "https://example.com/injected\nheader",
])
def test_paris_no_positive_offer_without_safe_dilicom_link(link, tmp_path, monkeypatch):
    session, account, _loan, client, gets = prepare(tmp_path, monkeypatch)
    client.digital = [raw_loan(link)]
    try:
        right = session.rights_observation_snapshot(account).rights[0]
        assert right.state == "unknown"
        assert right.delivery == "unknown" and right.delivery_offer_id == ""
        assert not right.delivery_attested
        assert gets == []
    finally:
        session.close()


@pytest.mark.parametrize("change,reads", [
    ({"explicit_user_action": False}, 0),
    ({"kind": "purchase"}, 0), ({"branch": "other"}, 0),
    ({"edition_id": "not-this-edition"}, 2),
    ({"right_id": "not-this-loan"}, 2),
])
def test_paris_invalid_selection_never_gets_content(
    change, reads, tmp_path, monkeypatch,
):
    session, account, _loan, client, gets = prepare(tmp_path, monkeypatch)
    client.digital = [raw_loan()]
    request = dict(edition_id="edition-1", right_id="loan-1", kind="loan",
                   branch="dilicom", explicit_user_action=True)
    request.update(change)
    try:
        with pytest.raises(ProviderCapabilityError):
            session.rights_selected_delivery_offer_snapshot(account, **request)
        assert gets == [] and len(client.calls) == reads
    finally:
        session.close()


def test_paris_missing_link_never_uses_borrow_post_for_candidate(
    tmp_path, monkeypatch,
):
    session, account, _loan, client, gets = prepare(tmp_path, monkeypatch)
    client.digital = [raw_loan(url="")]
    posted = []
    client.pnb_preferences = lambda: posted.append("PREFS")
    client.download_digital_document = lambda *_: posted.append("POST")
    try:
        with pytest.raises(ProviderCapabilityError):
            session.rights_selected_delivery_candidate(
                account, edition_id="edition-1", right_id="loan-1",
                kind="loan", branch="dilicom",
                delivery_offer_id="paris.pnb:" + "0" * 64,
                target_dir=tmp_path, explicit_user_action=True,
            )
        assert gets == [] and posted == []
        assert not list(tmp_path.iterdir())
    finally:
        session.close()
