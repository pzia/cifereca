from __future__ import annotations

from pathlib import Path

import pytest

from cifereca.plugin_api import Account, Loan, ProviderConfigurationError
from cifereca_paris import ParisProviderSession


ACSM = (
    b'<?xml version="1.0"?>'
    b'<fulfillmentToken xmlns="http://ns.adobe.com/adept">'
    b'<resourceItemInfo/>'
    b'</fulfillmentToken>'
)


class DummyConfig:
    def get(self, key, default=None):
        return default


class DummyDiag:
    def add(self, *args, **kwargs):
        return None


class Response:
    status_code = 200
    headers = {
        "content-type": "application/vnd.adobe.adept+xml",
        "content-disposition": 'attachment; filename="book.acsm"',
    }
    content = ACSM
    url = "https://eden.example/loan/book.acsm"

    def raise_for_status(self):
        return None


def _session() -> ParisProviderSession:
    return ParisProviderSession(
        credential_id="paris-default",
        label="Paris",
        credentials={"username": "u", "password": "p"},
        options={},
        config=DummyConfig(),
        diagnostics=DummyDiag(),
    )


@pytest.mark.parametrize(
    ("failed_upstream", "surviving_upstream", "loan_id"),
    [
        ("DILICOM", "SYRACUSE", "physical"),
        ("SYRACUSE", "DILICOM", "digital"),
    ],
)
def test_loans_keep_other_upstream_when_one_fails(
    monkeypatch,
    failed_upstream: str,
    surviving_upstream: str,
    loan_id: str,
):
    session = _session()
    account = Account(
        display_name="P",
        unique_id="",
        provider_id="paris",
        credential_id="paris-default",
    )
    session.connected = True
    session._accounts = [account]
    surviving = Loan(
        id=loan_id,
        provider=surviving_upstream,
        medium="physical" if surviving_upstream == "SYRACUSE" else "digital",
        record_id=loan_id,
        title=loan_id,
    )

    def fake_loans(provider, uid=""):
        if provider == failed_upstream:
            raise RuntimeError(f"{provider} down")
        return [surviving]

    monkeypatch.setattr(session.client, "loans", fake_loans)
    monkeypatch.setattr(session, "_enrich_digital_loan_metadata", lambda loan: None)
    try:
        rows = session.loans(account)
        assert [row.id for row in rows] == [loan_id]
        assert session.last_loan_errors == [{
            "upstream": failed_upstream,
            "code": "paris_loans_upstream_failed",
        }]
    finally:
        session.close()


def test_loans_fail_only_when_both_upstreams_fail(monkeypatch):
    session = _session()
    account = Account(
        display_name="P",
        unique_id="",
        provider_id="paris",
        credential_id="paris-default",
    )
    session.connected = True
    session._accounts = [account]
    monkeypatch.setattr(
        session.client,
        "loans",
        lambda provider, uid="": (_ for _ in ()).throw(RuntimeError(f"{provider} down")),
    )
    try:
        with pytest.raises(ProviderConfigurationError) as raised:
            session.loans(account)
        message = str(raised.value)
        assert "DILICOM" in message
        assert "SYRACUSE" in message
    finally:
        session.close()


def test_active_dilicom_loan_prefers_direct_ebook_url(monkeypatch, tmp_path: Path):
    session = _session()
    account = Account(
        display_name="P",
        unique_id="",
        provider_id="paris",
        credential_id="paris-default",
        is_primary=True,
    )
    loan = Loan(
        id="loan-direct",
        provider="DILICOM",
        medium="digital",
        record_id="9780000000001",
        title="Direct PNB",
        ebook_url="https://pnb-dilicom.example/v3/link/lib/LOAN/loan-direct/book.do?userAgentId=1",
        source_provider="paris",
        source_credential="paris-default",
        source_account_id="primary",
    )

    monkeypatch.setattr(
        session.client,
        "pnb_preferences",
        lambda: (_ for _ in ()).throw(AssertionError("preferences must not be requested")),
    )
    monkeypatch.setattr(
        session.client,
        "download_digital_document",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("fallback must not be called")),
    )
    requested: list[str] = []
    monkeypatch.setattr(session.client, "_validate_pnb_redirect_url", lambda url: None)
    monkeypatch.setattr(
        session.client,
        "_get_pnb_response",
        lambda url: requested.append(url) or Response(),
    )
    monkeypatch.setattr(session.client, "_pnb_trace_response", lambda *args, **kwargs: None)

    try:
        target = tmp_path / "paris"
        target.mkdir()
        path, source_url = session.download_digital(loan, account, target)
        assert requested == [loan.ebook_url]
        assert source_url == loan.ebook_url
        assert path.suffix == ".acsm"
        assert path.read_bytes() == ACSM
    finally:
        session.close()


def test_digital_download_falls_back_when_ebook_url_is_missing(monkeypatch, tmp_path: Path):
    session = _session()
    account = Account(
        display_name="P",
        unique_id="",
        provider_id="paris",
        credential_id="paris-default",
        is_primary=True,
    )
    loan = Loan(
        id="loan-fallback",
        provider="DILICOM",
        medium="digital",
        record_id="42",
        title="Fallback",
    )

    preferences = {"ReaderId": "r", "PassPhrase": "p", "Hint": "h"}
    monkeypatch.setattr(session.client, "pnb_preferences", lambda: preferences)
    calls: list[tuple[str, str, dict]] = []

    def fallback(current_loan, uid, prefs):
        calls.append((current_loan.id, uid, prefs))
        return "https://example.test/fallback.acsm"

    monkeypatch.setattr(session.client, "download_digital_document", fallback)
    monkeypatch.setattr(session.client, "_validate_pnb_redirect_url", lambda url: None)
    monkeypatch.setattr(session.client, "_get_pnb_response", lambda url: Response())
    monkeypatch.setattr(session.client, "_pnb_trace_response", lambda *args, **kwargs: None)

    try:
        target = tmp_path / "paris"
        target.mkdir()
        path, source_url = session.download_digital(loan, account, target)
        assert calls == [("loan-fallback", "", preferences)]
        assert source_url == "https://example.test/fallback.acsm"
        assert path.suffix == ".acsm"
        assert path.read_bytes() == ACSM
    finally:
        session.close()
