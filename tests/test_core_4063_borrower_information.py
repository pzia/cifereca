"""Synthetic contract tests; no real provider access."""
from time import time

from cifereca.core.borrower_information import (
    _display_values, prepare_borrower_information,
)


def test_display_projection_is_closed_and_never_admits():
    stamp = int(time())
    assert _display_values({
        "observed_at": stamp, "item_offer": "unavailable",
        "permission": "eligible", "remaining_quota": 4,
        "available_copies": 5, "pass_state": "active", "secret": "private",
    }) == {
        "observed_at": stamp, "item_offer": "unavailable",
        "permission": "unknown", "remaining_quota": None,
        "available_copies": None, "pass_state": "unknown",
    }


def test_invalid_timestamp_or_payload_fails_closed():
    for raw in (None, {}, {"observed_at": True},
                {"observed_at": int(time()) + 60}):
        assert _display_values(raw) is None
    assert _display_values({
        "observed_at": int(time()), "item_offer": "available",
    })["item_offer"] == "unknown"


def test_invalid_notice_does_not_access_service():
    class NoService:
        def __getattr__(self, name):
            raise AssertionError(name)

    assert prepare_borrower_information(
        NoService(), object(), identity_id="reader", account_id="account",
    ) is None
