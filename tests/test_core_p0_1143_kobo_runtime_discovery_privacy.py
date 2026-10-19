"""P0 #1143: an ADEPT runtime adapter's discovery failure is not a public error.

All probes are fakes, using a temporary SQLite service and no Adobe commands,
profile reads, network calls or live DRM operations.
"""
from __future__ import annotations

import json

from cifereca.providers import ProviderConfigurationError
from tests.test_kobo_adept_state import _service


SECRET = "https://private.invalid/LOAN/book.acsm?token=PRIVATE_RUNTIME_SECRET"


class HostileDiscoveryFailure(ProviderConfigurationError):
    def __str__(self):
        raise AssertionError("Do not stringify the private runtime failure")

    def __repr__(self):
        raise AssertionError("Do not repr the private runtime failure")


def test_adapter_failure_has_only_closed_error_code_and_safe_public_message(tmp_path):
    service = _service(tmp_path)
    calls = []
    def unavailable():
        calls.append("adapter")
        raise HostileDiscoveryFailure(SECRET)
    service._kobo_adapter = unavailable
    try:
        state = service.kobo_adept_state(force=True)
        assert state["available"] is False
        assert state["operational"] is False
        assert state["error"] == {
            "code": "runtime_plugin_unavailable",
            "message": "Runtime Kobo-ADEPT indisponible",
        }
        assert state["resolved_executable"] is None
        assert state["configured_command"] is None
        assert "PRIVATE_RUNTIME_SECRET" not in json.dumps(state)
        assert "private.invalid" not in json.dumps(state)
        assert service.kobo_adept_state() == state
        assert calls == ["adapter"]
        assert service.kobo_adept_state(force=True) == state
        assert calls == ["adapter", "adapter"]
    finally:
        service.close()


def test_raw_runtime_configuration_failure_is_not_returned_from_state(tmp_path):
    service = _service(tmp_path)
    service._kobo_adapter = lambda: (_ for _ in ()).throw(
        ProviderConfigurationError(SECRET)
    )
    try:
        status = service.kobo_adept_state()
        assert status["error"]["code"] == "runtime_plugin_unavailable"
        assert status["error"]["message"] == "Runtime Kobo-ADEPT indisponible"
        assert SECRET not in json.dumps(status)
    finally:
        service.close()
