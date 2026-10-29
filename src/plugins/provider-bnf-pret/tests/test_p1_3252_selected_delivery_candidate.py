"""#3252: selected BnF loan delivery produces only a guarded ACSM candidate.

Real BnF session with synthetic active-loans HTML; the token path is stubbed.
No actual provider GET/token, Core publication or ADEPT fulfillment.
"""
from pathlib import Path

import pytest

from cifereca.plugin_api import ProviderCapabilityError
from test_p1_3252_rights_observation_snapshot import fixture, activity
from test_p1_3252_bnf_signed_delivery_offer import signed_action


def setup():
    session, account, client = fixture(
        activity(("edition-A", "loan-A"), ("edition-B", "loan-B"))
        + signed_action("loan-A")
    )
    right = session.rights_selected_delivery_offer_snapshot(
        account, edition_id="edition-A", right_id="loan-A", kind="loan",
        branch="bnf_html", explicit_user_action=True,
    ).rights[0]
    return session, account, client, right


def selected(right, target_dir):
    return dict(
        edition_id=right.edition_id, right_id=right.right_id, kind="loan",
        branch="bnf_html", delivery_offer_id=right.delivery_offer_id,
        target_dir=target_dir, explicit_user_action=True,
    )


def test_bnf_selected_candidate_does_not_consult_v1_loans_facade(tmp_path):
    session, account, client, right = setup()
    try:
        # The v1 API is a compatibility reader; a canonical right admitted
        # by Core must never depend on its overridable result or side effects.
        def forbidden_v1(*args, **kwargs):
            pytest.fail("canonical candidate must not consult legacy loans()")

        received = []
        session.loans = forbidden_v1
        session.download_digital = lambda *args, **kwargs: (
            received.append((args, kwargs)) or (tmp_path / "candidate.acsm", "")
        )

        path, url = session.rights_selected_delivery_candidate(
            account, **selected(right, tmp_path),
        )
        assert path == tmp_path / "candidate.acsm" and url == ""
        assert client.calls == ["login", "activity", "activity"]
        assert len(received) == 1
        assert received[0][0][0].id == "loan-A"
        assert received[0][0][0].record_id == "edition-A"
        assert received[0][1]["expected_offer_id"] == right.delivery_offer_id
    finally:
        session.close()


def test_bnf_selected_candidate_one_loan_and_expected_offer(tmp_path):
    session, account, client, right = setup()
    try:
        received = []
        def deliver(loan, live, path, **kwargs):
            received.append((loan, live, path, kwargs))
            return (tmp_path / "candidate.acsm", "")
        session.download_digital = deliver
        result = session.rights_selected_delivery_candidate(
            account, **selected(right, tmp_path),
        )
        assert result == (tmp_path / "candidate.acsm", "")
        assert client.calls == ["login", "activity", "activity"]
        assert len(received) == 1
        loan, live, path, kwargs = received[0]
        assert (loan.id, loan.record_id) == ("loan-A", "edition-A")
        assert live is account
        assert path == tmp_path
        assert kwargs == {
            "explicit_user_action": True,
            "expected_offer_id": right.delivery_offer_id,
        }
    finally:
        session.close()


@pytest.mark.parametrize("change", [
    {"explicit_user_action": False},
    {"kind": "purchase"},
    {"branch": "other"},
    {"edition_id": ""},
    {"right_id": ""},
])
def test_bnf_selected_candidate_invalid_request_before_activity(tmp_path, change):
    session, account, client, right = setup()
    try:
        options = selected(right, tmp_path)
        options.update(change)
        before = list(client.calls)
        with pytest.raises(ProviderCapabilityError, match="selected_delivery_scope_mismatch"):
            session.rights_selected_delivery_candidate(account, **options)
        assert client.calls == before
    finally:
        session.close()


def test_bnf_selected_candidate_foreign_offer_before_network(tmp_path):
    session, account, client, right = setup()
    try:
        options = selected(right, tmp_path)
        options["delivery_offer_id"] = "bnf.reader_acs4:" + "0" * 64
        before = list(client.calls)
        with pytest.raises(ProviderCapabilityError, match="selected_delivery_owner_changed"):
            session.rights_selected_delivery_candidate(account, **options)
        assert client.calls == before
    finally:
        session.close()


def test_bnf_selected_candidate_owner_rebind_before_network(tmp_path):
    session, account, client, right = setup()
    try:
        session.identity_broker.identities = ("identity-B",)
        before = list(client.calls)
        with pytest.raises(ProviderCapabilityError, match="selected_delivery_owner_changed"):
            session.rights_selected_delivery_candidate(
                account, **selected(right, tmp_path),
            )
        assert client.calls == before
    finally:
        session.close()


def test_bnf_selected_candidate_signed_action_gone_no_token(tmp_path):
    session, account, client, right = setup()
    try:
        client.html = activity(("edition-A", "loan-A"))
        session.download_digital = lambda *a, **kw: pytest.fail("token GET")
        with pytest.raises(ProviderCapabilityError, match="selected_delivery_unobserved"):
            session.rights_selected_delivery_candidate(
                account, **selected(right, tmp_path),
            )
        assert client.calls == ["login", "activity", "activity"]
    finally:
        session.close()


def test_bnf_selected_candidate_owner_rebound_during_activity(tmp_path):
    session, account, client, right = setup()
    try:
        original = client.activity
        def rebind():
            result = original()
            session.identity_broker.identities = ("identity-B",)
            return result
        client.activity = rebind
        session.download_digital = lambda *a, **kw: pytest.fail("token GET")
        with pytest.raises(Exception, match="account_scope_mismatch"):
            session.rights_selected_delivery_candidate(
                account, **selected(right, tmp_path),
            )
        assert client.calls == ["login", "activity", "activity"]
    finally:
        session.close()


def test_bnf_selected_candidate_aba_broker_swap_after_activity_rejected(tmp_path):
    from test_p1_3252_rights_observation_snapshot import CoreOwnerBroker
    session, account, client, right = setup()
    try:
        read = client.activity
        def rebind_same_identity():
            html = read()
            session.identity_broker = CoreOwnerBroker(("identity-A",))
            return html
        client.activity = rebind_same_identity
        session.download_digital = lambda *a, **kw: pytest.fail("A→A token GET")
        with pytest.raises(Exception, match="account_scope_mismatch"):
            session.rights_selected_delivery_candidate(
                account, **selected(right, tmp_path),
            )
        assert client.calls == ["login", "activity", "activity"]
    finally:
        session.close()


def test_bnf_selected_candidate_duplicate_borrowers_refused_before_network(tmp_path):
    session, account, client, right = setup()
    try:
        session.identity_broker.identities = ("identity-A", "identity-A")
        before = list(client.calls)
        with pytest.raises(ProviderCapabilityError, match="selected_delivery_owner_changed"):
            session.rights_selected_delivery_candidate(
                account, **selected(right, tmp_path),
            )
        assert client.calls == before
    finally:
        session.close()


@pytest.mark.parametrize("mutated", [
    dict(credential_id="other-credential"),
    dict(unique_id="foreign-borrower"),
    dict(identity_id="identity-B"),
])
def test_bnf_selected_candidate_foreign_account_scope_no_get(tmp_path, mutated):
    from dataclasses import replace
    session, account, client, right = setup()
    try:
        foreign = replace(account, **mutated)
        before = list(client.calls)
        with pytest.raises(ProviderCapabilityError):
            session.rights_selected_delivery_candidate(
                foreign, **selected(right, tmp_path),
            )
        assert client.calls == before
    finally:
        session.close()
