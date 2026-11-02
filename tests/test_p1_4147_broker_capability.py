"""Regression #4147: retired reservations cannot be advertised via AccountRef."""
from types import SimpleNamespace

from cifereca.brokers import _account_ref


def test_legacy_booking_capability_is_not_exposed_by_public_account_broker():
    session = SimpleNamespace(
        connected=True,
        capabilities=SimpleNamespace(
            catalog=True,
            loans=True,
            history=True,
            bookings=True,  # Older installed plugin still declares this.
            direct_download=True,
            personal_library=False,
        ),
    )
    service = SimpleNamespace(
        _sessions={("paris", "old"): session},
        _session_key=lambda provider, credential: (provider, credential),
    )
    account = SimpleNamespace(
        provider_id="paris",
        credential_id="old",
        identity_id="alice",
        unique_id="paris:old:primary",
        display_name="Paris",
    )

    ref = _account_ref(service, account)

    assert "bookings" not in ref.capabilities
    assert set(ref.capabilities) == {"catalog", "loans", "history", "direct_download"}
    assert ref.connected is True
    assert ref.identity_id == "alice"
