from __future__ import annotations

from types import SimpleNamespace

from cifereca.brokers import DiagnosticsBrokerImpl
from cifereca.plugin_api import (
    DiagnosticsCheckProvider,
    HealthCheckResult,
    check_service_contract,
)


class Storage:
    def metadata_get(self, _key, default=None):
        return default


class CheckProvider:
    def checks(self):
        return (
            HealthCheckResult(
                "runtime.test",
                "runtime.test",
                "ok",
                "Runtime prêt",
                {
                    "authorization": "Bearer secret-value",
                    "url": "https://example.test/download?token=secret",
                    "resolved_executable": True,
                },
            ),
        )


class FailingCheckProvider:
    def checks(self):
        raise RuntimeError("secret should never escape")


class Manager:
    def services(self, category):
        if category == "provider.library":
            return (SimpleNamespace(plugin_id="provider.a"), SimpleNamespace(plugin_id="provider.b"))
        if category == "diagnostics.check":
            return (
                SimpleNamespace(plugin_id="diagnostics.test", service=CheckProvider()),
                SimpleNamespace(plugin_id="diagnostics.fail", service=FailingCheckProvider()),
            )
        return ()


class Service:
    def __init__(self):
        self.storage = Storage()
        self.accounts = [
            SimpleNamespace(
                identity_id="alice",
                provider_id="paris",
                credential_id="card",
                unique_id="account-1",
                display_name="Paris Alice",
                provider_label="Paris",
            )
        ]
        self._sessions = {
            self._session_key("paris", "card"): SimpleNamespace(
                connected=True,
                capabilities=SimpleNamespace(loans=True, personal_library=False),
            )
        }
        self.plugin_manager = Manager()

    @staticmethod
    def _session_key(provider, credential):
        return provider, credential

    def accounts_for_identity(self, identity_id=None):
        return [row for row in self.accounts if identity_id is None or row.identity_id == identity_id]


def test_diagnostics_check_contract_is_structural():
    assert isinstance(CheckProvider(), DiagnosticsCheckProvider)
    assert check_service_contract("diagnostics.check", CheckProvider()).ok


def test_diagnostics_broker_aggregates_unmasked_plugin_checks():
    rows = DiagnosticsBrokerImpl(Service()).active_checks()

    source = next(row for row in rows if row.message == "Source Paris Alice")
    assert source.status == "ok"
    assert source.detail["provider_id"] == "paris"

    providers = next(row for row in rows if row.check_id == "core.providers.loaded")
    assert providers.detail["count"] == 2

    plugin = next(row for row in rows if row.check_id == "runtime.test")
    assert plugin.detail["authorization"] == "Bearer secret-value"
    assert plugin.detail["url"] == "https://example.test/download?token=secret"
    assert plugin.detail["resolved_executable"] is True

    failed = next(row for row in rows if row.component == "diagnostics.fail")
    assert failed.status == "error"
    assert failed.detail == {"code": "diagnostic_check_failed"}
