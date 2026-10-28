"""#3633: real BnF HTTPX ACSM candidate, exact Core offer and session fences.

All HTTP is intercepted by MockTransport; no live account, loan, ADEPT
fulfillment, borrowing, Core SQLite or OriginLink publication.
"""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from bnf_token_fixture import connected, TOKEN
from cifereca.plugin_api import ProviderCapabilityError, ProviderError
from cifereca_bnf_pret_download import MAX_TOKEN_BYTES


class ExactOwner:
    def __init__(self, identities=("profile-a",)):
        self.identities = identities

    def accounts(self):
        return [
            NS(provider_id="bnf-pret", account_id="credential-a",
               identity_id=identity)
            for identity in self.identities
        ]


def ready(**transport):
    session, account, loan, state, calls, token_gets = connected(**transport)
    session.identity_broker = ExactOwner()
    observed = session.rights_selected_delivery_offer_snapshot(
        account, edition_id="book-a", right_id="loan-a", kind="loan",
        branch="bnf_html", explicit_user_action=True,
    )
    assert observed.coverage == "partial"
    assert len(observed.rights) == 1
    offer = observed.rights[0]
    assert offer.delivery == "available" and offer.state == "active"
    return session, account, state, calls, token_gets, offer


def selected(right, target_dir, **changes):
    data = dict(
        edition_id=right.edition_id, right_id=right.right_id,
        kind="loan", branch="bnf_html",
        delivery_offer_id=right.delivery_offer_id,
        target_dir=target_dir, explicit_user_action=True,
    )
    data.update(changes)
    return data


def stage(session, account, offer, path, **changes):
    return session.rights_selected_delivery_candidate(
        account, **selected(offer, path, **changes),
    )


def test_real_httpx_selected_bnf_acsm_to_exact_core_staging(tmp_path):
    session, account, _, calls, get, offer = ready()
    try:
        path, returned_url = stage(session, account, offer, tmp_path)
        assert path.is_file() and path.parent == tmp_path
        assert path.suffix == ".acsm" and path.read_bytes() == TOKEN
        assert returned_url == "" and len(get) == 1
        assert get[0].method == "GET"
        assert get[0].url.path == "/loans/loan-a"
        assert not any(p.suffix == ".epub" for p in tmp_path.iterdir())
        assert not any(p.suffix == ".part" for p in tmp_path.iterdir())
        # After offer preflight, candidate selects one exact page and a
        # second fresh page immediately before exactly ONE signed token GET.
        assert len([c for c in calls if c[0] == "GET" and
                    c[2] == "/my_profile/activity"]) == 4
    finally:
        session.close()


@pytest.mark.parametrize("changes", [
    {"right_id": "loan-b"}, {"edition_id": "book-b"},
    {"delivery_offer_id": "bnf.reader_acs4:" + "0" * 64},
    {"kind": "purchase"}, {"branch": "paris"},
    {"explicit_user_action": False},
])
def test_foreign_right_edition_offer_or_policy_refused_before_any_get(
    tmp_path, changes,
):
    session, account, _, calls, get, offer = ready()
    try:
        before = list(calls)
        with pytest.raises(ProviderCapabilityError):
            stage(session, account, offer, tmp_path, **changes)
        assert calls == before and get == []
        assert list(tmp_path.iterdir()) == []
    finally:
        session.close()


@pytest.mark.parametrize("location", ["missing", "symlink", "file", "none"])
def test_invalid_core_staging_denied_before_any_private_get(tmp_path, location):
    session, account, _, calls, get, offer = ready()
    path = tmp_path / "missing"
    if location == "symlink":
        path.symlink_to(tmp_path, target_is_directory=True)
    elif location == "file":
        path.write_text("not a directory")
    elif location == "none":
        path = None
    try:
        before = list(calls)
        with pytest.raises(ProviderCapabilityError, match="target_invalid"):
            stage(session, account, offer, path)
        assert calls == before and get == []
        assert not list(tmp_path.rglob("*.acsm"))
    finally:
        session.close()


@pytest.mark.parametrize("change", [
    "withdrawn", "unsigned", "reassigned", "duplicate", "closed",
])
def test_fresh_candidate_observation_refuses_before_token_get(
    tmp_path, change,
):
    session, account, state, _, token_gets, offer = ready()
    try:
        if change == "withdrawn":
            state["activity"] = '<section class="active-loans"></section>'
        elif change == "unsigned":
            state["activity"] = (
                '<section class="active-loans"><div class="js-loan">'
                '<a href="/resources/book-a">Book</a>'
                '<a href="/loans/loan-a">Loan</a></div></section>'
            )
        elif change == "reassigned":
            session.identity_broker.identities = ("profile-b",)
        elif change == "duplicate":
            session.identity_broker.identities = ("profile-a", "profile-a")
        else:
            session.close()
        with pytest.raises((ProviderCapabilityError, ProviderError, Exception)):
            stage(session, account, offer, tmp_path)
        assert token_gets == []
        assert not list(tmp_path.rglob("*.acsm"))
    finally:
        session.close()


def test_rotated_signed_offer_is_refetched_and_not_cached(tmp_path):
    session, account, state, _, token_gets, offer = ready()
    try:
        state["activity"] = state["activity"].replace(
            "syntheticNonce123", "rotatedNonce456",
        ).replace("0123456789abcdef0123456789abcdef01234567",
                  "abcdef0123456789abcdef01234567890123456789")
        fresh = session.rights_selected_delivery_offer_snapshot(
            account, edition_id="book-a", right_id="loan-a",
            kind="loan", branch="bnf_html", explicit_user_action=True,
        )
        assert fresh.rights[0].delivery_offer_id == offer.delivery_offer_id
        path, url = stage(session, account, offer, tmp_path)
        assert path.read_bytes() == TOKEN and url == ""
        assert len(token_gets) == 1
        assert "rotatedNonce456" in str(token_gets[0].url)
        assert "syntheticNonce123" not in str(token_gets[0].url)
    finally:
        session.close()


@pytest.mark.parametrize("when", ["second_activity", "token", "staging"])
@pytest.mark.parametrize("mutation", ["account_same_scope", "account_foreign",
                                      "provider_changed", "credential_changed"])
def test_rebound_session_instance_or_credential_never_stages_or_gets_stale_token(
    tmp_path, monkeypatch, when, mutation,
):
    session, account, state, calls, get, offer = ready()

    def rebind():
        if mutation == "account_same_scope":
            session._accounts = [replace(account)]
        elif mutation == "account_foreign":
            session._accounts = [replace(account, identity_id="profile-b")]
        elif mutation == "provider_changed":
            session.provider_id = "other-provider"
        else:
            session.credential_id = "other-credential"

    try:
        if when == "second_activity":
            original = session.client.activity
            seen = [0]

            def activity():
                result = original()
                seen[0] += 1
                if seen[0] == 2:
                    rebind()
                return result

            session.client.activity = activity
        elif when == "token":
            original = session.client.loan_token

            def get_and_rebind(*args):
                result = original(*args)
                rebind()
                return result

            session.client.loan_token = get_and_rebind
        else:
            namespace = session.download_digital.__func__.__globals__
            original = namespace["save_private_loan_token"]

            def save_and_rebind(*args):
                result = original(*args)
                rebind()
                return result

            monkeypatch.setitem(namespace, "save_private_loan_token",
                                save_and_rebind)

        with pytest.raises((ProviderError, ProviderCapabilityError), match=(
            "loan_not_observed|account_scope_mismatch|scope_changed"
            "|selected_delivery_owner_changed"
        )):
            stage(session, account, offer, tmp_path)
        assert len(get) == (0 if when == "second_activity" else 1)
        assert not list(tmp_path.rglob("*.acsm"))
        assert not list(tmp_path.rglob("*.part"))
    finally:
        session.close()


@pytest.mark.parametrize("status,headers,payload,reason", [
    (302, {"location": "https://pret.bnf.fr/loans/else"}, TOKEN,
     "download_redirect_refused"),
    (403, {}, b"", "access_denied"),
    (500, {}, b"", "http_unavailable"),
    (200, {"content-type": "text/html"}, b"<html>login</html>",
     "invalid_loan_token"),
    (200, {}, b"<!DOCTYPE forged>" + TOKEN, "invalid_loan_token"),
    (200, {"content-length": str(MAX_TOKEN_BYTES + 1)}, TOKEN, "token_limit"),
])
def test_http_failures_stay_in_core_staging_boundary(
    tmp_path, status, headers, payload, reason,
):
    session, account, _, _, get, offer = ready(
        payload=payload, status=status, headers=headers,
    )
    try:
        with pytest.raises(ProviderError, match=reason):
            stage(session, account, offer, tmp_path)
        assert len(get) == 1
        assert not list(tmp_path.rglob("*.acsm"))
        assert not list(tmp_path.rglob("*.part"))
    finally:
        session.close()
