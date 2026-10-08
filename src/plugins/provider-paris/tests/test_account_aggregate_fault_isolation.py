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


@pytest.mark.parametrize(
    ("failed_upstream", "expected_provider"),
    [
        ("DILICOM", "SYRACUSE"),
        ("SYRACUSE", "DILICOM"),
    ],
)
def test_all_bookings_keeps_healthy_backend(monkeypatch, failed_upstream, expected_provider):
    client = ParisClient(diagnostics=Diagnostics())

    def bookings(provider, _uid=""):
        if provider == failed_upstream:
            raise RuntimeError("unavailable")
        return [SimpleNamespace(provider=provider)]

    monkeypatch.setattr(client, "bookings", bookings)
    try:
        rows = client.all_bookings("id1")

        assert [row.provider for row in rows] == [expected_provider]
        assert client.last_booking_errors == [{
            "upstream": failed_upstream,
            "type": "RuntimeError",
        }]
    finally:
        client.close()


def test_all_bookings_raises_when_both_backends_fail(monkeypatch):
    client = ParisClient(diagnostics=Diagnostics())
    monkeypatch.setattr(
        client,
        "bookings",
        lambda _provider, _uid="": (_ for _ in ()).throw(RuntimeError("unavailable")),
    )
    try:
        with pytest.raises(ParisError, match="bookings indisponible"):
            client.all_bookings("id1")

        assert [row["upstream"] for row in client.last_booking_errors] == [
            "DILICOM",
            "SYRACUSE",
        ]
    finally:
        client.close()


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
        session.client.last_booking_errors = [{"upstream": "SYRACUSE", "type": "RuntimeError"}]

        state = session.diagnostic_state()

        assert state["history_errors"] == [{"upstream": "DILICOM", "type": "RuntimeError"}]
        assert state["booking_errors"] == [{"upstream": "SYRACUSE", "type": "RuntimeError"}]
    finally:
        session.close()
