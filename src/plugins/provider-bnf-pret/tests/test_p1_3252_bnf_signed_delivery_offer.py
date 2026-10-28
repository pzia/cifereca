"""#3252: real same-page BnF signed ACS4 action -> an opaque *offer*, not bytes.

Uses the actual provider session and HTML parser with synthetic accounts.
No SSO network, token GET, ADEPT, Core admission or SQLite publication.
"""
from dataclasses import asdict
import json

import pytest

from cifereca_bnf_pret_signed_action import reader_acs4_offer_pairs
from tests.test_p1_3252_rights_observation_snapshot import fixture, activity


def signed_action(loan, *, nonce="NONCE", token="SECRET_TOKEN"):
    return (
        '<a class="actions-list__link actions-list__sublink '
        'js-accordeon__item" '
        f'aria-controls="{loan}-reader-acs" '
        f'href="/loans/{loan}?device_type=reader&amp;drm=acs4'
        f'&amp;nonce={nonce}&amp;timestamp=T1&amp;token={token}">'
        'ADE</a>'
    )


def test_bnf_same_page_signed_action_qualifies_exact_offered_loan_only():
    html = activity(("edition-A", "loan-A"), ("edition-B", "loan-B"))
    html += signed_action("loan-A")
    session, account, client = fixture(html)
    try:
        result = session.rights_observation_snapshot(account)
        assert client.calls == ["login", "activity"]  # no signed token GET
        first, second = result.rights
        assert (first.edition_id, first.right_id, first.delivery) == (
            "edition-A", "loan-A", "available",
        )
        assert first.provider_attested and first.state == "active"
        assert first.delivery_offer_id.startswith("bnf.reader_acs4:")
        assert first.delivery_offer_id != first.right_id
        assert second.delivery == "unknown" and second.delivery_offer_id == ""
        assert result.coverage == "partial"
        assert not result.authorizes_mutation
        assert not result.authorizes_negative_reconciliation
        exported = json.dumps(asdict(result))
        assert "SECRET_TOKEN" not in exported
        assert "NONCE" not in exported
        assert "/loans/loan-A?" not in exported
        assert session._observed_loans == {
            "loan-A": "edition-A", "loan-B": "edition-B",
        }
    finally:
        session.close()


def test_bnf_signed_offer_id_stable_across_rotated_tokens_but_not_credentials():
    html = activity(("edition-A", "loan-A")) + signed_action("loan-A")
    session, account, client = fixture(html)
    try:
        a = session.rights_observation_snapshot(account).rights[0]
        client.html = activity(("edition-A", "loan-A")) + signed_action(
            "loan-A", token="ROTATED_SECRET", nonce="NEW_NONCE",
        )
        b = session.rights_observation_snapshot(account).rights[0]
        assert a.delivery == b.delivery == "available"
        assert a.delivery_offer_id == b.delivery_offer_id
    finally:
        session.close()


@pytest.mark.parametrize("extra", [
    "",
    signed_action("different-loan"),
    signed_action("loan-A").replace("drm=acs4", "drm=lcp"),
    signed_action("loan-A").replace("token=SECRET_TOKEN", "token="),
    signed_action("loan-A").replace("reader-acs", "reader-lcp"),
    signed_action("loan-A") + signed_action("loan-A", nonce="different"),
])
def test_bnf_unsigned_or_ambiguous_action_never_claims_available(extra):
    html = activity(("edition-A", "loan-A")) + extra
    session, account, client = fixture(html)
    try:
        result = session.rights_observation_snapshot(account)
        assert len(result.rights) == 1
        assert result.rights[0].delivery == "unknown"
        assert result.rights[0].delivery_offer_id == ""
        assert client.calls == ["login", "activity"]
    finally:
        session.close()


def test_bnf_signed_reader_parser_never_promotes_foreign_or_missing_loan():
    html = activity(("edition-A", "loan-A")) + signed_action("loan-B")
    assert reader_acs4_offer_pairs(
        html, (("loan-A", "edition-A"),),
    ) == frozenset()
    assert reader_acs4_offer_pairs(
        html, (("loan-B", "edition-B"),),
    ) == frozenset()
