"""#1120: ADEPT execution must not depend on post-execution telemetry."""
from __future__ import annotations

import logging
import time
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from cifereca.core.loan_adept_fulfillment import fulfill
from cifereca.core.loan_safe_trace import trace_integer


PRIVATE = "https://private.invalid/signed.acsm?token=NEVER_SHOW"


@pytest.mark.parametrize("failure", ["raises", "bad_map", "none", "valid"])
def test_successful_adept_fulfillment_does_not_fail_due_to_runtime_trace(
    caplog, failure,
):
    request = {"source": {"kind": "acsm"}}
    executed = Mock(return_value={
        "ok": True, "profile": "reader", "loan_id": "adept-loan",
    })
    class BrokenMap(dict):
        def get(self, _key):
            raise RuntimeError(PRIVATE)

    def diagnostic(plugin_id):
        assert plugin_id == "runtime.kobo-adept"
        if failure == "raises":
            raise RuntimeError(PRIVATE)
        if failure == "bad_map":
            return BrokenMap()
        if failure == "none":
            return None
        return {"returncode": 0, "duration_ms": 42}

    service = NS(
        _originating_adept_profile_id=Mock(return_value="profile-ref"),
        execute_acquisition_request=executed,
        runtime_diagnostic=diagnostic,
    )
    with caplog.at_level(logging.INFO, logger="cifereca.tests.loan-adept"):
        result, binding = fulfill(
            service, request, profile_name="reader",
            adept_borrower_identity_id="identity", trace_ref="opaque-ref",
            time=time, _TRACE_LOG=logging.getLogger("cifereca.tests.loan-adept"),
            trace_integer=trace_integer,
        )

    assert result["ok"] is True
    assert binding == {
        "adept_profile_id": "profile-ref", "adept_profile_name": "reader",
        "adept_loan_id": "adept-loan", "loan_state": "active",
        "return_available": True,
    }
    executed.assert_called_once_with(request)
    service._originating_adept_profile_id.assert_called_once_with(
        "identity", "reader",
    )
    logs = caplog.text
    assert "loan_ref=opaque-ref" in logs
    if failure == "valid":
        assert "returncode=0 duration_ms=42" in logs
    else:
        assert "returncode=None duration_ms=None" in logs
    assert PRIVATE not in logs + repr(result) + repr(binding)


def test_failed_adept_execution_still_propagates_without_reading_diagnostic():
    def fail(_request):
        raise RuntimeError("synthetic adobe execution failure")

    diagnostic = Mock(side_effect=AssertionError("must not query telemetry"))
    service = NS(
        _originating_adept_profile_id=lambda *_a: "profile-ref",
        execute_acquisition_request=fail,
        runtime_diagnostic=diagnostic,
    )
    with pytest.raises(RuntimeError, match="synthetic adobe execution failure"):
        fulfill(
            service, {}, profile_name="reader",
            adept_borrower_identity_id="identity", trace_ref="opaque-ref",
            time=time, _TRACE_LOG=logging.getLogger("cifereca.tests.loan-adept"),
            trace_integer=trace_integer,
        )
    diagnostic.assert_not_called()
