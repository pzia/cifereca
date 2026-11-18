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
        self.identities = [SimpleNamespace(identity_id="alice")]
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
        return [row for row in self.accounts if row.identity_id == identity_id]


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


def test_diagnostic_sources_use_all_registered_profiles_not_navigation():
    class Headless(Service):
        @property
        def current_identity(self):
            raise AssertionError("navigation is not diagnostic authority")

        @property
        def current_account(self):
            raise AssertionError("navigation is not diagnostic authority")

        def accounts_for_identity(self, identity_id=None):
            assert identity_id in {"alice", "bob"}
            requested.append(identity_id)
            return super().accounts_for_identity(identity_id)

    requested = []
    service = Headless()
    service.identities.append(SimpleNamespace(identity_id="bob"))
    service.accounts.append(SimpleNamespace(
        identity_id="bob", provider_id="paris", credential_id="other-card",
        unique_id="account-2", display_name="Paris Bob", provider_label="Paris",
    ))
    rows = DiagnosticsBrokerImpl(service).active_checks()
    assert requested == ["alice", "bob"]
    sources = {row.message: row for row in rows if row.message.startswith("Source ")}
    assert set(sources) == {"Source Paris Alice", "Source Paris Bob"}
    assert sources["Source Paris Alice"].status == "ok"
    assert sources["Source Paris Bob"].status == "warning"
    assert sources["Source Paris Alice"].check_id != sources["Source Paris Bob"].check_id


def test_diagnostic_duplicate_identity_registry_fails_closed():
    service = Service()
    service.identities.append(SimpleNamespace(identity_id="alice"))
    rows = DiagnosticsBrokerImpl(service).active_checks()
    sources = [row for row in rows if row.check_id == "core.sync.sources"]
    assert len(sources) == 1
    assert sources[0].status == "error"
    assert sources[0].detail == {"code": "sync_sources_unavailable"}
    assert not any(row.message.startswith("Source ") for row in rows)
