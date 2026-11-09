"""Provider-neutral projection never reauthorizes a consumed borrow claim."""
import json

import pytest

from cifereca.core.borrow_claim_checkpoint_view import observed_borrow_claim_state


def _row(**patch):
    payload = {
        "schema": 1, "operation": "loan.borrow",
        "operation_id": "synthetic-operation", "status": "confirmed",
        "request_accepted": True, "verification": "positive_active_loan",
        "verified_loan_id": "loan-123",
    }
    payload.update(patch)
    return json.dumps(payload)


@pytest.mark.parametrize(("raw", "expected"), [
    (None, ("no_checkpoint", False, False)),
    ("not json", ("opaque_consumed", True, False)),
    ("X" * 4097, ("opaque_consumed", True, False)),
    (_row(schema=True), ("opaque_consumed", True, False)),
    (_row(operation="purchase"), ("opaque_consumed", True, False)),
    (_row(operation_id=""), ("opaque_consumed", True, False)),
    (_row(status="confirmed", request_accepted="true"),
     ("opaque_consumed", True, False)),
    (_row(status="confirmed", verified_loan_id="loan/path"),
     ("opaque_consumed", True, False)),
    (_row(status="confirmed", verified_loan_id=""), ("opaque_consumed", True, False)),
    (_row(status="confirmed", verified_loan_id="loan-123"),
     ("confirmed", True, True)),
    (_row(status="request_sent"), ("request_sent", True, False)),
    (_row(status="submitted"), ("submitted", True, False)),
    (_row(status="blocked"), ("blocked", True, False)),
    (_row(status="verification_unavailable"), ("verification_unavailable", True, False)),
    (_row(status="unknown"), ("opaque_consumed", True, False)),
])
def test_borrow_checkpoint_is_read_only_and_fail_closed(raw, expected):
    assert observed_borrow_claim_state(raw) == expected


def test_observed_confirmation_is_not_borrow_retry_authorization():
    observed, consumed, confirmed = observed_borrow_claim_state(_row())
    assert (observed, consumed, confirmed) == ("confirmed", True, True)
    # The parser emits only these three values, never raw operation IDs,
    # entitlement IDs, a user credential or any retry permission.
    assert "synthetic-operation" not in repr((observed, consumed, confirmed))
