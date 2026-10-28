"""P1 #3222: Paris ListLoans 404 is an HTTP failure, NEVER an empty loan manifest.

All responses use httpx.MockTransport; there is no real account, network,
ADEPT operation, loan mutation or bearer token.
"""
from __future__ import annotations

import httpx
import pytest

from cifereca.plugin_api import Account, ProviderConfigurationError
from cifereca_paris import ParisProviderSession
from cifereca_paris_client import ParisClient, ParisLoanListError

PRIVATE = "SECRET_PRIVATE_ACCOUNT_3222"


class Diagnostics:
    def __init__(self):
        self.events = []

    def add(self, *args, **kwargs):
        self.events.append((args, kwargs))


class Config:
    def get(self, _name, default=None):
        return default


def _fixture(handler):
    diagnostics = Diagnostics()
    session = ParisProviderSession(
        credential_id="reader-private", label="Paris",
        credentials={"username": "private-login", "password": "private-password"},
        options={}, config=Config(), diagnostics=diagnostics,
    )
    account = Account(
        display_name="Private account", unique_id=PRIVATE, provider_id="paris",
        credential_id="reader-private", identity_id="identity-A",
    )
    old = session.client.client
    session.client.client = httpx.Client(
        transport=httpx.MockTransport(handler), follow_redirects=True,
    )
    old.close()
    session.connected = True
    session._accounts = [account]
    return session, account, diagnostics


def _empty():
    return httpx.Response(200, json={"success": True, "d": {"Loans": []}})


@pytest.mark.parametrize(("status", "reason"), (
    (404, "paris_loans_http_not_found"),
    (401, "paris_loans_http_unauthorized"),
    (403, "paris_loans_http_forbidden"),
    (429, "paris_loans_http_rate_limited"),
    (503, "paris_loans_http_unavailable"),
))
def test_listloans_http_denial_is_typed_without_retry_or_secret(status, reason, caplog):
    requests = []
    def handler(request):
        requests.append(request)
        assert request.method == "GET"
        assert request.url.path.endswith("/ListLoans")
        assert request.url.params.get("serviceCode") in ("DILICOM", "SYRACUSE")
        assert request.url.params.get("userUniqueIdentifier") == PRIVATE
        return httpx.Response(status, text="<html>" + PRIVATE + "</html>",
                              headers={"content-type": "text/html"})

    session, account, diag = _fixture(handler)
    try:
        with pytest.raises(ProviderConfigurationError):
            session.loans(account)
        assert len(requests) == 2  # no automatic retry on a 404/other denial
        assert session.last_loan_errors == [
            {"upstream": "DILICOM", "code": "paris_loans_upstream_failed",
             "reason_code": reason},
            {"upstream": "SYRACUSE", "code": "paris_loans_upstream_failed",
             "reason_code": reason},
        ]
        events = repr(diag.events)
        assert reason in events
        # HTTP denial emits the observed status and MIME plus its closed
        # exception reason; no unobserved HTML body is invented in the log.
        assert f"'http_status': {status}" in events
    finally:
        session.close()


@pytest.mark.parametrize(("reply", "reason"), (
    ("html", "paris_loans_html_unexpected"),
    ("redirect_html", "paris_loans_redirect_unexpected"),
    ("redirect_json", "paris_loans_redirect_unexpected"),
    ("not_json", "paris_loans_non_json"),
    ("json_list", "paris_loans_json_shape_invalid"),
))
def test_bad_html_and_redirects_never_count_as_empty_loans(reply, reason):
    calls = []
    def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/private-login":
            if reply == "redirect_json":
                # Valid JSON at a *different* URL is never ListLoans evidence.
                return _empty()
            return httpx.Response(200, text="<html>login</html>",
                                  headers={"content-type": "text/html"})
        if request.url.params.get("serviceCode") == "SYRACUSE":
            return _empty()
        if reply == "html":
            return httpx.Response(200, text="<html>unauthorized</html>",
                                  headers={"content-type": "text/html"})
        if reply in ("redirect_html", "redirect_json"):
            return httpx.Response(302, headers={"location": "/private-login"})
        if reply == "json_list":
            return httpx.Response(200, json=[])
        return httpx.Response(200, text="bad json",
                              headers={"content-type": "application/json"})

    session, account, diag = _fixture(handler)
    try:
        snapshot = session.loan_inventory_snapshot(account)
        assert snapshot.status == "partial"
        assert snapshot.loans == ()
        assert [(b.upstream, b.completeness) for b in snapshot.branches] == [
            ("dilicom", "partial"), ("syracuse", "unknown"),
        ]
        # Typed snapshots do not write the mutable legacy error facade.
        assert session.last_loan_errors == []
        assert sum(path.endswith("/ListLoans") for path in calls) == 2
        assert reason in repr(diag.events)
    finally:
        session.close()


def test_both_404_then_upstream_recovers_without_claiming_absence():
    broken = {"value": True}
    def handler(request):
        if broken["value"]:
            return httpx.Response(404, text="<html>absent</html>")
        return _empty()

    session, account, diag = _fixture(handler)
    try:
        with pytest.raises(ProviderConfigurationError):
            session.loan_inventory_snapshot(account)
        assert session.last_loan_errors == []
        broken["value"] = False
        snapshot = session.loan_inventory_snapshot(account)
        assert snapshot.status == "success"
        assert snapshot.loans == ()
        # Even a valid empty Paris response does not attest exhaustive absence.
        assert all(branch.completeness == "unknown" for branch in snapshot.branches)
        assert session.last_loan_errors == []
    finally:
        session.close()


def test_listloans_transport_error_is_not_an_empty_manifest():
    def handler(request):
        if request.url.params.get("serviceCode") == "DILICOM":
            raise httpx.ConnectTimeout("timeout-private", request=request)
        return _empty()

    session, account, diag = _fixture(handler)
    try:
        result = session.loan_inventory_snapshot(account)
        assert result.status == "partial"
        assert result.loans == ()
        assert session.last_loan_errors == []
    finally:
        session.close()


def test_listloans_error_type_cannot_carry_arbitrary_message():
    with pytest.raises(ValueError, match="paris_loans_reason_invalid"):
        ParisLoanListError("private-login=" + PRIVATE, status=404)



@pytest.mark.parametrize("failure", ["json_error_message", "json_error_list"])
def test_listloans_supplier_error_is_preserved_with_structured_code(
    failure, caplog,
):
    # ListLoans error text actually emitted by _json remains available
    # alongside typed error/status in the diagnostic journal.
    requests = []

    def handler(request):
        requests.append(request)
        body = {"success": False}
        if failure == "json_error_message":
            body["message"] = "Borrower " + PRIVATE
        else:
            body["errors"] = ["Signed URL: https://private.invalid/" + PRIVATE]
        return httpx.Response(200, json=body)

    session, account, diagnostics = _fixture(handler)
    try:
        with pytest.raises(ProviderConfigurationError):
            session.loan_inventory_snapshot(account)
        assert len(requests) == 2
        assert all(request.url.path.endswith("/ListLoans") for request in requests)
        assert PRIVATE in repr(diagnostics.events)
        assert "loans DILICOM" in repr(diagnostics.events)
        assert "loans SYRACUSE" in repr(diagnostics.events)
        # Raw diagnostics remain searchable; the typed producer does not
        # mutate the legacy v1 side channel to report the same failure.
        assert session.last_loan_errors == []
    finally:
        session.close()



def test_listloans_raw_error_is_searchable_and_exported_without_truncation(
    tmp_path, caplog,
):
    """Real Core Diagnostics journal + HTTPX mock, no external provider."""
    import json
    import logging

    from cifereca import logging_utils
    from cifereca.core.diagnostic_journal import query_journal, export_raw_journal
    from cifereca.diagnostics import Diagnostics as CoreDiagnostics

    # Deliberately longer than the obsolete 240-char truncation.
    raw = "Synthetic ListLoans cause: " + ("café-error-segment-" * 22)
    mime = "application/json; charset=utf-8; profile=synthetic-loans"
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={"success": False, "message": raw},
            headers={"content-type": mime},
        )

    session, account, _fake = _fixture(handler)
    log_path = tmp_path / "paris-diagnostics.log"
    handler_file = logging_utils.PrivateRotatingFileHandler(
        log_path, maxBytes=1_000_000, backupCount=1, encoding="utf-8",
    )
    handler_file.setFormatter(logging_utils.CanonicalEventFormatter(
        "%(asctime)s %(levelname)s %(name)s [%(threadName)s] %(message)s"
    ))
    logger = logging.getLogger("cifereca")
    prior = logger.level
    logger.setLevel(logging.INFO)
    logger.addHandler(handler_file)
    session.client.diagnostics = CoreDiagnostics(level="trace")
    try:
        with caplog.at_level(logging.INFO):
            with pytest.raises(ProviderConfigurationError):
                session.loan_inventory_snapshot(account)
        assert len(requests) == 2
        assert all(req.url.path.endswith("/ListLoans") for req in requests)
        assert mime in caplog.text
        assert raw in caplog.text

        found = query_journal(log_path, search="café-error-segment", limit=20)
        assert found["total"] == 2
        assert {event["operation"] for event in found["events"]} == {
            "loans DILICOM", "loans SYRACUSE",
        }
        assert all(
            event["detail"]["message"] == "loans " +
            event["operation"].split(" ")[1] + ": " + raw
            for event in found["events"]
        )
        assert all(event["status"] == "error" for event in found["events"])
        exported = json.loads(export_raw_journal(log_path))
        assert sum(raw in json.dumps(row, ensure_ascii=False)
                   for row in exported["events"]) == 2
        # Raw diagnostics remain searchable; the typed producer does not
        # mutate the legacy v1 side channel to report the same failure.
        assert session.last_loan_errors == []
    finally:
        session.close()
        logger.removeHandler(handler_file)
        logger.setLevel(prior)
        handler_file.close()
