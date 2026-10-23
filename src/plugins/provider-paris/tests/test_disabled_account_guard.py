from __future__ import annotations

import pytest
import httpx

from cifereca.plugin_api import ProviderConfigurationError
from cifereca_paris import ParisProviderSession


class DummyConfig:
    def get(self, key, default=None):
        return default


class RecordingDiagnostics:
    def __init__(self):
        self.rows = []

    def add(self, *args, **kwargs):
        self.rows.append((args, kwargs))


def _session(diagnostics=None) -> ParisProviderSession:
    return ParisProviderSession(
        credential_id="paris-default",
        label="Paris",
        credentials={"username": "u", "password": "p"},
        options={},
        config=DummyConfig(),
        diagnostics=diagnostics or RecordingDiagnostics(),
    )


@pytest.mark.parametrize(
    "summary",
    [
        {"IsDisabled": True},
        {"IsBlocked": True},
        {"IsActive": False},
        {"Status": "disabled"},
        {"State": "Désactivé"},
        {"MembershipStatus": "expired"},
    ],
)
def test_connect_rejects_explicitly_disabled_account_before_pairings(monkeypatch, summary):
    diagnostics = RecordingDiagnostics()
    session = _session(diagnostics)
    monkeypatch.setattr(session.client, "login", lambda username, password: {"success": True})
    monkeypatch.setattr(session.client, "summary", lambda provider, uid: dict(summary))
    pairings_called = []
    monkeypatch.setattr(session.client, "pairings", lambda: pairings_called.append(True) or [])

    try:
        with pytest.raises(ProviderConfigurationError) as raised:
            session.connect()

        message = str(raised.value)
        assert "compte désactivé ou inutilisable" in message
        assert "avant ADEPT" in message
        assert pairings_called == []
        assert session.connected is False
        assert session.account_status["usable"] is False
        assert any(
            args[:3] == ("AUTH", "account status", "error")
            and kwargs.get("detail", {}).get("usable") is False
            for args, kwargs in diagnostics.rows
        )
    finally:
        session.close()


def test_connect_keeps_account_when_no_explicit_disabled_signal(monkeypatch):
    session = _session()
    monkeypatch.setattr(session.client, "login", lambda username, password: {"success": True})
    monkeypatch.setattr(
        session.client,
        "summary",
        lambda provider, uid: {"DisplayName": "Lecteur", "Barcode": "123", "Status": "active"},
    )
    monkeypatch.setattr(session.client, "pairings", lambda: [])

    try:
        accounts = session.connect()

        assert len(accounts) == 1
        assert accounts[0].display_name == "Lecteur"
        assert session.connected is True
        assert session.account_status == {"usable": True, "reason": None}
        assert session.diagnostic_state()["account_status"]["usable"] is True
        assert session.diagnostic_state()["pairings"] == {"complete": True, "error": None}
    finally:
        session.close()


def test_connect_falls_back_to_dilicom_summary_when_syracuse_is_unavailable(monkeypatch):
    diagnostics = RecordingDiagnostics()
    session = _session(diagnostics)
    monkeypatch.setattr(session.client, "login", lambda username, password: {"success": True})
    attempts = []

    def summary(provider, uid):
        attempts.append((provider, uid))
        if provider == "SYRACUSE":
            raise RuntimeError("syracuse unavailable")
        return {"DisplayName": "Lecteur numérique", "Barcode": "456", "Status": "active"}

    monkeypatch.setattr(session.client, "summary", summary)
    monkeypatch.setattr(session.client, "pairings", lambda: [])

    try:
        accounts = session.connect()

        assert attempts == [("SYRACUSE", ""), ("DILICOM", "")]
        assert len(accounts) == 1
        assert accounts[0].display_name == "Lecteur numérique"
        assert session.connected is True
        assert session.account_status == {"usable": True, "reason": None}
        assert any(
            args[:3] == ("API", "summary SYRACUSE", "error")
            and kwargs.get("detail", {}).get("upstream") == "SYRACUSE"
            and kwargs.get("detail", {}).get("error") == "RuntimeError"
            and "message" not in kwargs.get("detail", {})
            for args, kwargs in diagnostics.rows
        )
    finally:
        session.close()


def test_connect_preserves_first_summary_error_when_both_backends_fail(monkeypatch):
    diagnostics = RecordingDiagnostics()
    session = _session(diagnostics)
    monkeypatch.setattr(session.client, "login", lambda username, password: {"success": True})
    attempts = []

    def summary(provider, uid):
        attempts.append((provider, uid))
        raise RuntimeError(f"{provider} unavailable")

    monkeypatch.setattr(session.client, "summary", summary)
    pairings_called = []
    monkeypatch.setattr(session.client, "pairings", lambda: pairings_called.append(True) or [])

    try:
        with pytest.raises(RuntimeError, match="SYRACUSE unavailable"):
            session.connect()

        assert attempts == [("SYRACUSE", ""), ("DILICOM", "")]
        assert pairings_called == []
        assert session.connected is False
        errors = [
            kwargs.get("detail", {})
            for args, kwargs in diagnostics.rows
            if args[:2] in {("API", "summary SYRACUSE"), ("API", "summary DILICOM")}
        ]
        assert [row.get("upstream") for row in errors] == ["SYRACUSE", "DILICOM"]
        assert all("message" not in row for row in errors)
    finally:
        session.close()


def test_unknown_status_does_not_false_positive():
    session = _session()
    try:
        assert session._account_unusable_reason({"Status": "renewal_pending"}) is None
        assert session._account_unusable_reason({}) is None
    finally:
        session.close()


def test_connect_reports_pairings_failure_without_losing_primary_account(monkeypatch):
    diagnostics = RecordingDiagnostics()
    session = _session(diagnostics)
    monkeypatch.setattr(session.client, "login", lambda username, password: {"success": True})
    monkeypatch.setattr(
        session.client,
        "summary",
        lambda provider, uid: {"DisplayName": "Lecteur", "Barcode": "123", "Status": "active"},
    )

    def fail_pairings():
        raise RuntimeError("pairings unavailable")

    monkeypatch.setattr(session.client, "pairings", fail_pairings)

    try:
        accounts = session.connect()

        assert len(accounts) == 1
        assert accounts[0].display_name == "Lecteur"
        assert session.connected is True
        assert session.pairing_status == {"complete": False, "error": "RuntimeError"}
        assert session.diagnostic_state()["pairings"] == {
            "complete": False,
            "error": "RuntimeError",
        }
        assert any(
            args[:3] == ("API", "pairings", "error")
            and kwargs.get("detail", {}).get("error") == "RuntimeError"
            and "message" not in kwargs.get("detail", {})
            for args, kwargs in diagnostics.rows
        )
    finally:
        session.close()


def test_pairings_status_recovers_after_next_successful_connect(monkeypatch):
    session = _session()
    monkeypatch.setattr(session.client, "login", lambda username, password: {"success": True})
    monkeypatch.setattr(
        session.client,
        "summary",
        lambda provider, uid: {"DisplayName": "Lecteur", "Barcode": "123", "Status": "active"},
    )
    attempts = iter((RuntimeError("temporary"), []))

    def pairings():
        value = next(attempts)
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(session.client, "pairings", pairings)

    try:
        session.connect()
        assert session.pairing_status["complete"] is False

        session.connect()
        assert session.pairing_status == {"complete": True, "error": None}
    finally:
        session.close()


def test_connect_marks_partial_pairings_as_incomplete(monkeypatch):
    session = _session()
    monkeypatch.setattr(session.client, "login", lambda username, password: {"success": True})
    monkeypatch.setattr(
        session.client,
        "summary",
        lambda provider, uid: {"DisplayName": "Lecteur", "Barcode": "123", "Status": "active"},
    )
    linked = type("Linked", (), {
        "display_name": "Lié",
        "barcode": "456",
        "unique_id": "linked-1",
        "is_primary": False,
        "provider_id": "",
        "credential_id": "",
        "provider_label": "",
    })()

    def partial_pairings():
        session.client.last_pairing_errors = [{"upstream": "DILICOM", "type": "RuntimeError"}]
        return [linked]

    monkeypatch.setattr(session.client, "pairings", partial_pairings)

    try:
        accounts = session.connect()

        assert len(accounts) == 2
        assert session.pairing_status == {"complete": False, "error": "RuntimeError"}
    finally:
        session.close()


def test_login_failure_emits_closed_phase_without_secret(monkeypatch):
    diagnostics = RecordingDiagnostics()
    session = _session(diagnostics)
    secret = "PRIVATE_PASSWORD_OR_COOKIE"
    def fail_login(*args, **kwargs):
        raise RuntimeError(secret)
    monkeypatch.setattr(session.client, "login", fail_login)
    monkeypatch.setattr(
        session.client, "summary",
        lambda *_: pytest.fail("summary must not run after failed login"),
    )
    try:
        with pytest.raises(RuntimeError, match=secret):
            session.connect()
        assert session.connected is False
        assert (
            ("AUTH", "Paris connection", "error"),
            {"detail": {"provider": "paris", "code": "paris_login_failed"}},
        ) in diagnostics.rows
        assert secret not in repr(diagnostics.rows)
        assert "username" not in repr(diagnostics.rows)
        assert "password" not in repr(diagnostics.rows)
    finally:
        session.close()


def test_both_summary_failures_emit_closed_phase_without_secret(monkeypatch):
    diagnostics = RecordingDiagnostics()
    session = _session(diagnostics)
    secret = "SIGNED_URL_OR_SECRET"
    monkeypatch.setattr(session.client, "login", lambda *_: {"success": True})
    monkeypatch.setattr(
        session.client, "summary",
        lambda *_: (_ for _ in ()).throw(RuntimeError(secret)),
    )
    try:
        with pytest.raises(RuntimeError, match=secret):
            session.connect()
        assert (
            ("AUTH", "Paris connection", "error"),
            {"detail": {"provider": "paris", "code": "paris_summary_unavailable"}},
        ) in diagnostics.rows
        assert secret not in repr(diagnostics.rows)
        assert session.connected is False
    finally:
        session.close()


@pytest.mark.parametrize(("status", "json_payload", "expected"), [
    (403, None, "paris_login_http_forbidden"),
    (401, None, "paris_login_http_unauthorized"),
    (429, None, "paris_login_rate_limited"),
    (503, None, "paris_login_upstream_unavailable"),
    (200, {"success": False, "message": "PRIVATE_UPSTREAM_TOKEN"},
     "paris_login_rejected"),
])
def test_paris_connect_exposes_closed_http_reason_not_private_body(
    monkeypatch, status, json_payload, expected,
):
    diagnostics = RecordingDiagnostics()
    session = _session(diagnostics)
    secret = "PRIVATE_UPSTREAM_TOKEN"
    def post(*args, **kwargs):
        assert kwargs["data"]["username"] == "u"
        return httpx.Response(
            status, json=json_payload if json_payload is not None
            else {"message": secret},
            request=httpx.Request("POST", "https://bibliotheques.paris.fr/login"),
        )
    monkeypatch.setattr(session.client.client, "post", post)
    monkeypatch.setattr(session.client, "summary", lambda *_:
                        pytest.fail("no summary after failed login"))
    try:
        with pytest.raises(Exception):
            session.connect()
        assert session.connected is False
        assert any(
            args[:3] == ("AUTH", "Paris connection", "error")
            and kwargs.get("detail") == {"provider": "paris", "code": expected}
            for args, kwargs in diagnostics.rows
        )
        assert secret not in repr(diagnostics.rows)
        assert "username" not in repr(diagnostics.rows)
        assert "password" not in repr(diagnostics.rows)
        assert not any(
            args[:2] == ("API", "summary SYRACUSE")
            for args, kwargs in diagnostics.rows
        )
    finally:
        session.close()


def test_untyped_paris_login_failure_stays_generic(monkeypatch):
    diagnostics = RecordingDiagnostics()
    session = _session(diagnostics)
    monkeypatch.setattr(
        session.client, "login",
        lambda *_: (_ for _ in ()).throw(RuntimeError("SECRET_URL")),
    )
    try:
        with pytest.raises(RuntimeError):
            session.connect()
        assert (
            ("AUTH", "Paris connection", "error"),
            {"detail": {"provider": "paris", "code": "paris_login_failed"}},
        ) in diagnostics.rows
        assert "SECRET_URL" not in repr(diagnostics.rows)
    finally:
        session.close()


@pytest.mark.parametrize(("status", "expected"), [
    (403, "paris_summary_http_forbidden"),
    (401, "paris_summary_http_unauthorized"),
    (429, "paris_summary_rate_limited"),
    (502, "paris_summary_upstream_unavailable"),
])
def test_paris_summary_http_failure_diagnostic_is_closed_and_scoped(
    monkeypatch, status, expected,
):
    diagnostics = RecordingDiagnostics()
    session = _session(diagnostics)
    secret = "PRIVATE_ACCOUNT_OR_COOKIE"
    monkeypatch.setattr(session.client, "login", lambda *_: {"success": True})
    def get(*args, **kwargs):
        return httpx.Response(
            status, json={"message": secret},
            request=httpx.Request("GET", "https://bibliotheques.paris.fr/private/"),
        )
    monkeypatch.setattr(session.client.client, "get", get)
    monkeypatch.setattr(session.client, "pairings", lambda: pytest.fail(
        "pairings must not run after two summary errors",
    ))
    try:
        with pytest.raises(Exception):
            session.connect()
        assert session.connected is False
        for upstream in ("SYRACUSE", "DILICOM"):
            assert any(
                args[:3] == ("API", f"summary {upstream}", "error")
                and kwargs.get("detail", {}).get("code") == expected
                for args, kwargs in diagnostics.rows
            )
            assert any(
                args[:3] == ("API", f"summary {upstream}", "error")
                and len(args) > 4 and args[4].get("code") == expected
                for args, kwargs in diagnostics.rows
            )
        assert secret not in repr(diagnostics.rows)
        assert any(
            args[:3] == ("AUTH", "Paris connection", "error")
            and kwargs.get("detail", {}).get("code") == "paris_summary_unavailable"
            for args, kwargs in diagnostics.rows
        )
    finally:
        session.close()


def test_paris_summary_syracuse_403_dilicom_ok_still_connects(monkeypatch):
    diagnostics = RecordingDiagnostics()
    session = _session(diagnostics)
    monkeypatch.setattr(session.client, "login", lambda *_: {"success": True})
    def get(*args, **kwargs):
        source = kwargs["params"].get("provider")
        if source == "SYRACUSE":
            return httpx.Response(
                403, json={"message": "PRIVATE"},
                request=httpx.Request("GET", "https://bibliotheques.paris.fr/"),
            )
        return httpx.Response(
            200, json={
                "success": True,
                "d": {"AccountSummary": {"DisplayName": "Lecteur", "Status": "active"}},
            }, request=httpx.Request("GET", "https://bibliotheques.paris.fr/"),
        )
    monkeypatch.setattr(session.client.client, "get", get)
    monkeypatch.setattr(session.client, "pairings", lambda: [])
    try:
        accounts = session.connect()
        assert len(accounts) == 1
        assert session.connected is True
        assert any(
            args[:2] == ("API", "summary SYRACUSE")
            and kwargs.get("detail", {}).get("code") == "paris_summary_http_forbidden"
            for args, kwargs in diagnostics.rows
        )
        assert "PRIVATE" not in repr(diagnostics.rows)
    finally:
        session.close()
