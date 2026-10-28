"""#3252: selected BnF rights use the same provider-neutral shape as Numilog.

Authenticated HTML and signed ACS4 action are synthetic. No token GET, ADEPT,
final EPUB, Core consent or SQLite publication is claimed.
"""
from dataclasses import asdict
import json

import pytest

from cifereca.plugin_api import ProviderCapabilityError
from test_p1_3252_rights_observation_snapshot import fixture, activity
from test_p1_3252_bnf_signed_delivery_offer import signed_action


def selection(**overrides):
    return {
        **dict(edition_id="edition-A", right_id="loan-A",
               kind="loan", branch="bnf_html",
               explicit_user_action=True),
        **overrides,
    }


def test_bnf_selected_offer_from_multiple_loans_is_scoped_to_one():
    html = activity(("edition-A", "loan-A"), ("edition-B", "loan-B"))
    html += signed_action("loan-A")
    session, account, client = fixture(html)
    try:
        selected = session.rights_selected_delivery_offer_snapshot(
            account, **selection(),
        )
        assert client.calls == ["login", "activity"]
        assert selected.scope.identity_id == "identity-A"
        assert selected.scope.credential_id == "credential-A"
        assert selected.scope.account_id == "credential-A"
        assert selected.branch == "bnf_html"
        assert len(selected.rights) == 1
        only = selected.rights[0]
        assert (only.edition_id, only.right_id, only.kind) == (
            "edition-A", "loan-A", "loan",
        )
        assert only.state == "active" and only.provider_attested
        assert only.delivery == "available"
        assert only.delivery_offer_id.startswith("bnf.reader_acs4:")
        assert selected.coverage == "partial"
        assert not selected.authorizes_mutation
        assert not selected.authorizes_negative_reconciliation
        text = json.dumps(asdict(selected))
        assert "SECRET_TOKEN" not in text and "/loans/loan-A?" not in text
    finally:
        session.close()


@pytest.mark.parametrize("change", [
    {"explicit_user_action": False},
    {"kind": "purchase"},
    {"branch": "different-branch"},
    {"edition_id": ""},
    {"right_id": ""},
])
def test_bnf_invalid_selected_key_refused_without_activity_get(change):
    session, account, client = fixture(
        activity(("edition-A", "loan-A")) + signed_action("loan-A")
    )
    try:
        with pytest.raises(ProviderCapabilityError, match="selected_offer_scope_mismatch"):
            session.rights_selected_delivery_offer_snapshot(
                account, **selection(**change),
            )
        assert client.calls == ["login"]
    finally:
        session.close()


@pytest.mark.parametrize("pairs", [
    (),
    (("edition-B", "loan-A"),),
    (("edition-A", "loan-B"),),
])
def test_bnf_no_exact_observed_right_cannot_be_selected(pairs):
    session, account, client = fixture(activity(*pairs))
    try:
        with pytest.raises(ProviderCapabilityError, match="selected_offer_scope_changed"):
            session.rights_selected_delivery_offer_snapshot(
                account, **selection(),
            )
        assert client.calls == ["login", "activity"]
    finally:
        session.close()


def test_bnf_selected_unsigned_right_is_unknown_not_missing():
    session, account, client = fixture(activity(("edition-A", "loan-A")))
    try:
        snapshot = session.rights_selected_delivery_offer_snapshot(
            account, **selection(),
        )
        assert snapshot.rights[0].state == "active"
        assert snapshot.rights[0].delivery == "unknown"
        assert snapshot.rights[0].delivery_offer_id == ""
        assert client.calls == ["login", "activity"]
    finally:
        session.close()
