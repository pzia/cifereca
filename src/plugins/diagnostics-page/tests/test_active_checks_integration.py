from __future__ import annotations

from cifereca.plugin_api import HealthCheckResult

from cifereca_diagnostics_page import active_checks


class Diagnostics:
    def __init__(self):
        self.calls = 0

    def active_checks(self):
        self.calls += 1
        return (
            HealthCheckResult(
                "core.sync.a",
                "paris",
                "ok",
                "Source Paris Alexandre",
                {"provider_id": "paris", "connected": True, "sync_capable": True},
                "core",
            ),
            HealthCheckResult(
                "runtime.kobo-adept",
                "runtime.kobo-adept",
                "ok",
                "Kobo-Adept",
                {"contract_version": 8, "resolved_executable": True},
            ),
        )


def test_active_checks_are_presentation_only():
    broker = Diagnostics()
    rows = active_checks(broker)

    assert broker.calls == 1
    assert rows[0]["message"] == "Source Paris Alexandre"
    assert rows[0]["detail"]["provider_id"] == "paris"
    assert rows[1]["component"] == "runtime.kobo-adept"
    assert rows[1]["detail"]["resolved_executable"] is True
