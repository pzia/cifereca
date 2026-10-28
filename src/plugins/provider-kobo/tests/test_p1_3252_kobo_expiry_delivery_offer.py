"""#3252: expired Vault descriptor routes are not executable delivery offers.

No real Vault HTTP, ACSM GET, ADEPT, receipt or Core SQLite. The provider
supplies an absolute timezone-aware expiration, not a caller-side promise.
"""
from __future__ import annotations

from copy import deepcopy

import pytest

from cifereca_kobo_delivery_offer import exact_kobo_offer_id
from tests.test_p1_3252_kobo_descriptor_response_scope import (
    descriptor, SCOPE,
)
from tests.test_p1_3252_kobo_selected_delivery_offer import setup


PAST = "2020-01-01T00:00:00Z"
FUTURE = "2099-12-31T23:59:59+00:00"


def offer(row):
    return exact_kobo_offer_id(row, **SCOPE)


@pytest.mark.parametrize("path", [
    ("root", "expires_at"),
    ("root_validity", "expires_at"),
    ("acquisition", "expires_at"),
    ("acquisition_validity", "expires_at"),
    ("candidate", "expires_at"),
    ("candidate_validity", "expires_at"),
])
@pytest.mark.parametrize("expiry", [
    PAST, "2020-01-01T02:00:00+02:00",
    "untrusted", "2030-01-01", "", False, 0,
    "2026-10-28T00:00:00",  # missing timezone, never assume UTC
])
def test_expired_or_invalid_claim_cannot_make_one_available_offer(path, expiry):
    payload = descriptor()
    if path[0] == "root":
        target = payload
    elif path[0] == "root_validity":
        target = payload.setdefault("validity", {})
    elif path[0] == "acquisition":
        target = payload["acquisition"]
    elif path[0] == "acquisition_validity":
        target = payload["acquisition"].setdefault("validity", {})
    elif path[0] == "candidate":
        target = payload["acquisition"]["candidates"][0]
    else:
        target = payload["acquisition"]["candidates"][0].setdefault("validity", {})
    target[path[1]] = expiry
    assert offer(payload) == ""


@pytest.mark.parametrize("path", [
    "root", "root_validity", "acquisition", "acquisition_validity",
    "candidate", "candidate_validity",
])
def test_fresh_expiry_is_allowed_only_as_volatile_offer_not_file_proof(path):
    payload = descriptor()
    if path == "root":
        target = payload
    elif path == "root_validity":
        target = payload.setdefault("validity", {})
    elif path == "acquisition":
        target = payload["acquisition"]
    elif path == "acquisition_validity":
        target = payload["acquisition"].setdefault("validity", {})
    elif path == "candidate":
        target = payload["acquisition"]["candidates"][0]
    else:
        target = payload["acquisition"]["candidates"][0].setdefault("validity", {})
    target["expires_at"] = FUTURE
    assert offer(payload).startswith("kobo.descriptor:")


@pytest.mark.parametrize("explicit", [None])
def test_unspecified_expiry_not_a_false_negative_for_legacy_vault(explicit):
    original = descriptor()
    copy = deepcopy(original)
    copy["acquisition"]["validity"] = {
        "expires_at": explicit, "renewable": True,
        "refresh_strategy": "resolve_again",
    }
    assert offer(copy) == offer(original)


def test_kobo_session_conservatively_returns_unknown_on_stale_post_expiry():
    s, vault, broker, account, calls = setup()
    try:
        def expired(product_id, **kwargs):
            calls.append("POST")
            response = descriptor()
            response["acquisition"]["product_id"] = product_id
            response["acquisition"]["account_id"] = account.unique_id
            response["acquisition"]["validity"] = {"expires_at": PAST}
            return response
        vault.acquisition = expired
        snapshot = s.rights_delivery_offer_snapshot(
            account, "book-1", explicit_user_action=True,
        )
        assert vault.calls == 1 and calls == ["POST"]
        assert len(snapshot.rights) == 1
        assert snapshot.rights[0].state == "active"  # purchase != expired link
        assert snapshot.rights[0].delivery == "unknown"
        assert snapshot.rights[0].delivery_offer_id == ""
        assert not snapshot.authorizes_mutation
    finally:
        s.close()
