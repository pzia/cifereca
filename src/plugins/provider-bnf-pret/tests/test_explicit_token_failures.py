"""Failures never infer rights or publish an ACSM from a BnF Web response."""
from pathlib import Path

import pytest

from bnf_token_fixture import connected, TOKEN
from cifereca.plugin_api import ProviderError
from cifereca_bnf_pret_download import MAX_TOKEN_BYTES

INVALID_XML = b'<html>fake result</html>'


@pytest.mark.parametrize("payload,status,headers,reason", [
    (INVALID_XML, 200, {"content-type": "text/html"}, "invalid_loan_token"),
    (INVALID_XML, 200, {}, "invalid_loan_token"),
    (b"", 200, {}, "invalid_loan_token"),
    (TOKEN, 302, {"location": "https://pret.bnf.fr/loans/else"}, "download_redirect_refused"),
    (b"", 401, {}, "auth_required"),
    (b"", 403, {}, "access_denied"),
    (b"", 429, {}, "rate_limited"),
    (TOKEN, 200, {"content-length": str(MAX_TOKEN_BYTES + 1)}, "token_limit"),
    (b"x" * (MAX_TOKEN_BYTES + 1), 200, {}, "token_limit"),
    (b'<!DOCTYPE token [<!ENTITY x "PRIVATE">]>' + TOKEN, 200, {}, "invalid_loan_token"),
    (TOKEN.replace(b'fulfillmentType="loan"', b'fulfillmentType="buy"'),
     200, {}, "invalid_loan_token"),
    (b'<fulfillmentToken xmlns="https://evil.invalid" fulfillmentType="loan"/>',
     200, {}, "invalid_loan_token"),
])
def test_bad_token_or_http_failure_is_one_get_without_saved_artifact(
    payload, status, headers, reason, tmp_path,
):
    session, account, loan, _, _, downloaded = connected(
        payload=payload, status=status, headers=headers,
    )
    try:
        with pytest.raises(ProviderError, match=reason):
            session.download_digital(loan, account, tmp_path, explicit_user_action=True)
        assert len(downloaded) == 1
        assert downloaded[0].method == "GET"
        assert not list(tmp_path.rglob("*.acsm"))
        assert not list(tmp_path.rglob("*.part"))
    finally:
        session.close()


@pytest.mark.parametrize("status,reason", [
    (401, "activity_auth_required"),
    (403, "activity_access_denied"),
    (429, "activity_rate_limited"),
    (503, "activity_http_unavailable"),
])
def test_activity_failure_is_distinct_from_token_get_and_never_downloads(
    tmp_path, status, reason,
):
    import httpx
    from cifereca_bnf_pret_http import ACTIVITY
    session, account, loan, state, calls, downloaded = connected()
    original = session.client.http._transport.transport.handler
    activity_requests = []

    def handler(request):
        if str(request.url) == ACTIVITY:
            activity_requests.append(request)
            return httpx.Response(status, content=b"synthetic private failure",
                                  headers={"content-type": "text/html"})
        return original(request)

    session.client.http._transport.transport.handler = handler
    try:
        with pytest.raises(ProviderError, match="^" + reason + "$"):
            session.download_digital(loan, account, tmp_path, explicit_user_action=True)
        assert len(activity_requests) == 1
        assert downloaded == []
        assert not list(tmp_path.rglob("*.acsm"))
    finally:
        session.close()


def test_activity_bad_dom_and_unknown_exception_are_not_token_failures(tmp_path):
    from cifereca_bnf_pret_http import ReaderHTTPError
    session, account, loan, state, calls, downloaded = connected()
    try:
        state["activity"] = "<html>synthetic sign-in</html>"
        with pytest.raises(ProviderError, match="^activity_unrecognized_loans$"):
            session.download_digital(loan, account, tmp_path, explicit_user_action=True)
        session.client.activity = lambda: (_ for _ in ()).throw(
            ReaderHTTPError("synthetic PRIVATE cookie or signed URL"))
        with pytest.raises(ProviderError, match="^activity_unavailable$"):
            session.download_digital(loan, account, tmp_path, explicit_user_action=True)
        assert downloaded == []
        assert not list(tmp_path.rglob("*.acsm"))
    finally:
        session.close()


def test_close_during_fresh_activity_disallows_subsequent_token_get(tmp_path):
    session, account, loan, state, calls, downloaded = connected()
    html = state["activity"]

    def activity():
        session.close()
        return html

    session.client.activity = activity
    with pytest.raises(ProviderError, match="^session_closed$"):
        session.download_digital(loan, account, tmp_path, explicit_user_action=True)
    assert downloaded == []
    assert not list(tmp_path.rglob("*.acsm"))


def test_symlinked_staging_directory_is_refused(tmp_path):
    session, account, loan, _, _, downloaded = connected()
    shortcut = tmp_path / "alias"
    shortcut.symlink_to(tmp_path / "destination", target_is_directory=True)
    try:
        with pytest.raises(ProviderError, match="download_storage_failed"):
            session.download_digital(loan, account, shortcut, explicit_user_action=True)
        assert len(downloaded) == 1
        assert not (tmp_path / "destination").exists()
    finally:
        session.close()
