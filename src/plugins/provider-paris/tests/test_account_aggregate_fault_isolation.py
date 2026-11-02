from __future__ import annotations

from types import SimpleNamespace

import pytest

from cifereca_paris import ParisProviderSession
from cifereca_paris_client import ParisClient, ParisError


class DummyConfig:
    def get(self, key, default=None):
        return default


class Diagnostics:
    def add(self, *args, **kwargs):
        return None


@pytest.mark.parametrize(
    ("failed_upstream", "expected_provider"),
    [
        ("DILICOM", "SYRACUSE"),
        ("SYRACUSE", "DILICOM"),
    ],
)
def test_all_history_keeps_healthy_backend(monkeypatch, failed_upstream, expected_provider):
    client = ParisClient(diagnostics=Diagnostics())

    def history(provider, _uid=""):
        if provider == failed_upstream:
            raise RuntimeError("unavailable")
        return [SimpleNamespace(provider=provider, borrowed_at="2026-01-01", returned_at=None)]

    monkeypatch.setattr(client, "history", history)
    try:
        rows = client.all_history("id1")

        assert [row.provider for row in rows] == [expected_provider]
        assert client.last_history_errors == [{
            "upstream": failed_upstream,
            "type": "RuntimeError",
        }]
    finally:
        client.close()


def test_all_history_raises_when_both_backends_fail(monkeypatch):
    client = ParisClient(diagnostics=Diagnostics())
    monkeypatch.setattr(
        client,
        "history",
        lambda _provider, _uid="": (_ for _ in ()).throw(RuntimeError("unavailable")),
    )
    try:
        with pytest.raises(ParisError, match="history indisponible"):
            client.all_history("id1")

        assert [row["upstream"] for row in client.last_history_errors] == [
            "DILICOM",
            "SYRACUSE",
        ]
    finally:
        client.close()


def test_retired_booking_api_is_absent_while_history_remains_operational():
    # Installed older credentials must not reinstate Paris ListBookings or the
    # CancelBookings mutation. This is an API surface check; no network call.
    from cifereca_paris import DESCRIPTOR
    from cifereca_paris_normalize import normalize_loan, normalize_history
    from cifereca.plugin_api import ProviderSession, ProviderCapabilityError
    import cifereca_paris_normalize as normalizers

    assert DESCRIPTOR.capabilities.bookings is False
    # Public Plugin API v1 keeps an inherited default-deny method. Removing
    # the Paris override restores that refusal without a network side effect.
    assert "bookings" not in ParisProviderSession.__dict__
    assert ParisProviderSession.bookings is ProviderSession.bookings
    session = object.__new__(ParisProviderSession)
    session.provider_id = "paris"
    with pytest.raises(ProviderCapabilityError, match="bookings unsupported"):
        session.bookings(None)
    assert not hasattr(ParisClient, "bookings")
    assert not hasattr(ParisClient, "all_bookings")
    assert not hasattr(ParisClient, "cancel_booking")
    assert not hasattr(normalizers, "normalize_booking")
    assert callable(normalize_loan) and callable(normalize_history)


def test_diagnostic_state_exposes_partial_account_aggregations():
    session = ParisProviderSession(
        credential_id="paris-default",
        label="Paris",
        credentials={"username": "u", "password": "p"},
        options={},
        config=DummyConfig(),
        diagnostics=Diagnostics(),
    )
    try:
        session.client.last_history_errors = [{"upstream": "DILICOM", "type": "RuntimeError"}]

        state = session.diagnostic_state()

        assert state["history_errors"] == [{"upstream": "DILICOM", "type": "RuntimeError"}]
        assert "booking_errors" not in state
    finally:
        session.close()
