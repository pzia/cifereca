"""#2900 — diagnostic sites/EAN without borrower, loan or signed URL disclosure."""
from __future__ import annotations

import logging
from types import SimpleNamespace

import httpx
import pytest

from cifereca_paris_trace import (
    public_book_id, public_content_type, public_http_status, public_site,
)


@pytest.mark.parametrize(("candidate", "expected"), [
    ("9782000000000", "9782000000000"),
    ("0" * 13, "0" * 13),
    ("reader@example.test", "unavailable"),
    ("loan-SUPERPRIVATE", "unavailable"),
    ("9782000000000?token=SECRET", "unavailable"),
    ("９７８２０００００００００", "unavailable"),
    (None, "unavailable"),
    (1234567890123, "unavailable"),
])
def test_only_exact_ascii_public_ean_is_logged(candidate, expected):
    assert public_book_id(candidate) == expected


@pytest.mark.parametrize(("url", "site"), [
    ("https://bibliotheques.paris.fr/numerique/?token=SUPERPRIVATE", "bibliotheques.paris.fr"),
    ("https://acs4.cantook.net/LOAN/SUPERPRIVATE?sig=SUPERPRIVATE", "acs4.cantook.net"),
    ("https://user:SUPERPRIVATE@acs4.cantook.net/file", "unknown"),
    ("http://acs4.cantook.net/file", "unknown"),
    ("https://acs4.cantook.net:8443/file", "unknown"),
    ("https://reader.SUPERPRIVATE.syracuse.cloud/path", "external"),
    ("https://acs4.cantook.net.evil.test/private", "external"),
    ("https://xn--not-approved.test/private", "external"),
    ("javascript:alert(1)", "unknown"),
    (None, "unknown"),
])
def test_site_is_closed_public_host_not_the_original_url(url, site):
    result = public_site(url)
    assert result == site
    assert "SUPERPRIVATE" not in result


def test_untrusted_http_response_metadata_is_never_copied_verbatim():
    assert public_http_status(403) == 403
    assert public_http_status("403; password=SECRET") == "unknown"
    assert public_http_status(True) == "unknown"
    assert public_content_type("text/html; charset=utf-8") == "text/html"
    assert public_content_type("text/html; token=SUPERPRIVATE") == "text/html"
    assert public_content_type("text/html;charset=foo@example.test") == "text/html"
    assert public_content_type("arbitrary/SUPERPRIVATE") == "other"


def test_client_download_trace_has_site_and_book_but_no_private_loan_or_uid(caplog):
    from cifereca_paris_client import ParisClient

    seen = []
    def handler(request):
        seen.append(request)
        return httpx.Response(
            200, json={"success": True, "d": "https://acs4.cantook.net/private?token=SUPERPRIVATE"},
            headers={"content-type": "application/json; SECRET=SUPERPRIVATE"},
        )
    client = ParisClient(
        client_factory=lambda: httpx.Client(transport=httpx.MockTransport(handler)),
    )
    loan = SimpleNamespace(
        id="loan-SUPERPRIVATE", provider="DILICOM", record_id="9782000000000",
    )
    try:
        with caplog.at_level(logging.INFO, logger="cifereca"):
            result = client.download_digital_document(
                loan, uid="user-SUPERPRIVATE", preferences={
                    "ReaderId": "reader-SUPERPRIVATE",
                    "PassPhrase": "pass-SUPERPRIVATE", "Hint": "hint-SUPERPRIVATE",
                },
            )
        assert "token=SUPERPRIVATE" in result  # private business result remains unchanged
        assert len(seen) == 1
        logs = "\n".join(record.getMessage() for record in caplog.records)
        assert "book_id=9782000000000" in logs
        assert "site=bibliotheques.paris.fr" in logs
        assert "status=200" in logs
        assert "content_type=application/json" in logs
        for secret in ("SUPERPRIVATE", "loan_id=", "account_key=", "user-SUPERPRIVATE"):
            assert secret not in logs
    finally:
        client.close()


@pytest.mark.parametrize("direct", [True, False])
def test_provider_download_trace_omits_loan_account_and_filename(
    monkeypatch, caplog, tmp_path, direct,
):
    import cifereca_paris

    target_url = "https://acs4.cantook.net/private/SUPERPRIVATE?sig=SUPERPRIVATE"
    response = SimpleNamespace(
        url=target_url, status_code=200,
        headers={"content-type": "application/vnd.adobe.adept+xml; token=SUPERPRIVATE",
                 "content-length": "SECRET-SUPERPRIVATE"},
        content=b"synthetic acsm",
        raise_for_status=lambda: None,
    )

    class FakeClient:
        base_url = "https://bibliotheques.paris.fr"
        def pnb_preferences(self):
            return {"ReaderId": "reader-SUPERPRIVATE", "PassPhrase": "SUPERPRIVATE",
                    "Hint": "SUPERPRIVATE"}
        def download_digital_document(self, *_args):
            return target_url
        def _validate_pnb_redirect_url(self, url):
            assert url == target_url
        def _get_pnb_response(self, _url):
            return response
        def _pnb_trace_response(self, *_args, **_kwargs):
            return None

    monkeypatch.setattr(
        cifereca_paris, "inspect_artifact_content",
        lambda *_args, **_kwargs: SimpleNamespace(valid=True),
    )
    monkeypatch.setattr(
        cifereca_paris, "publish_artifact_content",
        lambda path, data: path.write_bytes(data),
    )
    loan = SimpleNamespace(
        id="loan-SUPERPRIVATE", record_id="9782000000000",
        source_account_id="account-SUPERPRIVATE",
        ebook_url=target_url if direct else "",
        title="title-SUPERPRIVATE", provider="DILICOM",
    )
    account = SimpleNamespace(is_primary=False, unique_id="user-SUPERPRIVATE")
    session = SimpleNamespace(client=FakeClient())
    with caplog.at_level(logging.INFO, logger="cifereca"):
        # Isolate the existing transport logging contract with a fake
        # transport. The public download_digital entrypoint now enforces the
        # real authenticated account/loan/Core-owner binding separately.
        path, returned_url = cifereca_paris.ParisProviderSession._download_digital_scoped_impl(
            session, loan, account, tmp_path,
        )
    assert path.exists()
    assert returned_url == target_url
    logs = "\n".join(record.getMessage() for record in caplog.records)
    assert "book_id=9782000000000" in logs
    assert "site=acs4.cantook.net" in logs
    assert "status=200" in logs
    for secret in ("SUPERPRIVATE", "loan_id=", "account_key=", "file=",
                   "content_length=", "sig="):
        assert secret not in logs


def test_untrusted_redirect_location_does_not_publish_private_host_or_path(caplog):
    from cifereca_paris_client import ParisClient

    response = SimpleNamespace(
        url="https://bibliotheques.paris.fr/numerique/",
        status_code=302,
        headers={
            "location": "https://private-SUPERPRIVATE.syracuse.cloud/LOAN/SUPERPRIVATE?token=SUPERPRIVATE"
        },
    )
    with caplog.at_level(logging.INFO, logger="cifereca"):
        ParisClient._pnb_trace_response(response, hop=2, elapsed_ms=17)
    text = "\n".join(record.getMessage() for record in caplog.records)
    assert "target_site=external" in text
    assert "status=302" in text
    assert "hop=2" in text
    assert "SUPERPRIVATE" not in text
    assert "LOAN/" not in text
