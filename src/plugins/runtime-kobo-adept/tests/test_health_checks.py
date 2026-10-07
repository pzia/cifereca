from __future__ import annotations

from cifereca.plugin_api import DiagnosticsCheckProvider

from cifereca_kobo_adept_health import KoboAdeptHealthChecks


class Runtime:
    def __init__(self, available=True, resolved="/usr/bin/kobo-adept"):
        self._available = available
        self._resolved = resolved

    def available(self):
        return self._available

    def probe(self):
        return {
            "contract": {"contract_version": 8},
            "resolved_executable": self._resolved,
        }


class Context:
    def __init__(self, runtime):
        self.runtime = runtime

    def service(self, plugin_id):
        assert plugin_id == "runtime.kobo-adept"
        return self.runtime


def test_kobo_adept_health_provider_contract_and_safe_shape():
    provider = KoboAdeptHealthChecks().activate(Context(Runtime()))
    assert isinstance(provider, DiagnosticsCheckProvider)
    row = provider.checks()[0]
    assert row.status == "ok"
    assert row.detail == {
        "available": True,
        "contract_version": 8,
        "resolved_executable": True,
    }


def test_kobo_adept_health_reports_missing_runtime_binary():
    provider = KoboAdeptHealthChecks().activate(Context(Runtime(available=False)))
    row = provider.checks()[0]
    assert row.status == "error"
    assert row.detail == {"available": False}
