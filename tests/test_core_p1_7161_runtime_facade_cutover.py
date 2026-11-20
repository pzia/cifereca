"""Regression: removed Kobo adapter is not a hidden dependency (#7161)."""
from tests.test_sync_state_contract import _service


def test_runtime_state_missing_plugin_fails_closed(tmp_path):
    service = _service(tmp_path)
    try:
        state = service.kobo_adept_state(force=True)
        assert state["available"] is False
        assert state["error"]["code"] == "runtime_plugin_unavailable"
    finally:
        service.close()


def test_runtime_state_uses_installed_adapter_without_legacy_facade(tmp_path):
    service = _service(tmp_path)
    try:
        calls = []

        class Runtime:
            required_contract = 8

            def available(self):
                return True

            def probe_core(self):
                calls.append("probe")
                return {
                    "contract": {"contract_version": 8, "capabilities": []},
                    "capabilities": {"software_version": "0.13.6",
                                     "contract_version": 8,
                                     "capabilities": {"download": {
                                         "acsm": {"available": True},
                                     }}},
                }

        adapter = Runtime()
        service._required_plugin_service = lambda plugin_id: (
            adapter if plugin_id == "runtime.kobo-adept" else None
        )
        result = service.kobo_adept_state(force=True)
        assert result["available"] is True
        assert result["compatible"] is True
        assert result["acsm_available"] is True
        assert result["operational"] is True
        assert calls == ["probe"]
    finally:
        service.close()
