"""#3723: BnF Core guarded v2 candidate honors revocation during private ACSM reads.

The activity pages, signed actions, token response, and staging are synthetic.
No network, ADEPT, Core SQLite publication or real provider credentials.
"""
from __future__ import annotations

import pytest

from cifereca.plugin_api import DeliveryCandidate, ProviderCapabilityError, ProviderError
from test_p1_3252_selected_delivery_candidate import setup


def request(right, target_dir):
    return {
        "edition_id": right.edition_id,
        "resource_id": right.right_id,
        "kind": "loan",
        "branch": "bnf_html",
        "selected_offer_id": right.delivery_offer_id,
        "target_dir": target_dir,
        "explicit_user_action": True,
    }


def test_guarded_candidate_rejects_revoked_lease_before_private_get(tmp_path):
    session, account, client, right = setup()
    try:
        before = list(client.calls)
        with pytest.raises(ProviderCapabilityError, match="bnf_core_admission_revoked"):
            session.downloadable_selected_delivery_candidate_guarded(
                account, **request(right, tmp_path),
                admission_current=lambda: False,
            )
        assert client.calls == before
        assert list(tmp_path.iterdir()) == []
    finally:
        session.close()


def test_guarded_candidate_composes_core_lease_without_legacy_unscoped_get(tmp_path):
    session, account, client, right = setup()
    try:
        admitted = lambda: True
        delegated = []

        def fake_download(loan, live, target, **kwargs):
            assert loan.id == right.right_id and live is account
            delegated.append(kwargs)
            path = target / "signed-candidate.acsm"
            path.write_bytes(b"synthetic ACSM candidate")
            return path, ""

        session.download_digital = fake_download
        result = session.downloadable_selected_delivery_candidate_guarded(
            account, **request(right, tmp_path), admission_current=admitted,
        )
        assert type(result) is DeliveryCandidate
        assert result.source.scope.account_id == account.unique_id
        assert result.path == tmp_path / "signed-candidate.acsm"
        assert result.selected_offer_id == right.delivery_offer_id
        assert delegated == [{
            "explicit_user_action": True,
            "expected_offer_id": right.delivery_offer_id,
            "admission_current": admitted,
        }]
        assert client.calls == ["login", "activity", "activity"]
    finally:
        session.close()


def test_revocation_during_fresh_activity_prevents_token_get(tmp_path):
    session, account, client, right = setup()
    try:
        live = [True]
        activity = client.activity

        def revoke_after_activity():
            html = activity()
            live[0] = False
            return html

        client.activity = revoke_after_activity
        session.download_digital = lambda *_args, **_kwargs: pytest.fail(
            "revoked Core consent must not initiate token GET"
        )
        with pytest.raises((ProviderError, ProviderCapabilityError)):
            session.downloadable_selected_delivery_candidate_guarded(
                account, **request(right, tmp_path),
                admission_current=lambda: live[0],
            )
        assert live[0] is False
        assert client.calls == ["login", "activity", "activity"]
        assert list(tmp_path.iterdir()) == []
    finally:
        session.close()


def test_signed_token_receives_composed_live_core_lease(tmp_path):
    session, account, client, right = setup()
    try:
        live = [True]
        token_calls = []

        def token(loan_id, signed_url, *, admission_current):
            assert loan_id == right.right_id
            assert admission_current() is True
            token_calls.append(signed_url)
            live[0] = False
            assert admission_current() is False
            # Even a valid-looking token must not reach private staging
            # once the Core source generation has changed mid-request.
            return b"synthetic intercepted token"

        client.loan_token = token
        with pytest.raises(ProviderError, match="loan_not_observed"):
            session.downloadable_selected_delivery_candidate_guarded(
                account, **request(right, tmp_path),
                admission_current=lambda: live[0],
            )
        assert len(token_calls) == 1
        assert list(tmp_path.iterdir()) == []
    finally:
        session.close()
