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
