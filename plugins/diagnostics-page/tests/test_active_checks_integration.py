from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from cifereca.brokers import SynchronizationBrokerImpl
from cifereca.plugin_api import (
    PluginContext,
    PluginDirectories,
    ResolvedPluginService,
)

from cifereca_diagnostics_page import active_checks


class Storage:
    def __init__(self):
        self.values = {}
        self.writes = []

    def metadata_get(self, key):
        return self.values.get(key)

    def metadata_set(self, key, value):
        self.writes.append((key, value))
        self.values[key] = value


class Runtime:
    def available(self):
        return True

    def probe(self):
        return {
            "contract": {"contract_version": "1.2"},
            "resolved_executable": "/usr/bin/kobo-adept",
        }


class FakeService:
    def __init__(self):
        self.storage = Storage()
        self.account = SimpleNamespace(
            identity_id="alexandre",
            provider_id="paris",
            credential_id="paris-main",
            unique_id="reader-1",
            display_name="Paris Alexandre",
            provider_label="Bibliothèques de Paris",
        )
        self.accounts = [self.account]
        self._sessions = {
            self._session_key("paris", "paris-main"): SimpleNamespace(
                connected=True,
                capabilities=SimpleNamespace(
                    catalog=True,
                    loans=True,
                    history=True,
                    bookings=False,
                    direct_download=False,
                    personal_library=False,
                ),
            )
        }
        self.sync_calls = 0

    @staticmethod
    def _session_key(provider, credential):
        return provider, credential

    def accounts_for_identity(self, identity_id=None):
        if identity_id is None or identity_id == "alexandre":
            return list(self.accounts)
        return []

    def synchronize_account_source(self, account, deep=False, control=None):
        self.sync_calls += 1
        return {"loans": 1}


class Diagnostics:
    def snapshot(self):
        return ()

    def system_status(self):
        return {}

    def export_text(self):
        return ""


def _resolved(plugin_id, category_id, service, contract_id, contract_version="1.0"):
    return ResolvedPluginService(
        plugin_id=plugin_id,
        plugin_version="1.0",
        package_id="org.cifereca.test",
        package_version="1.0",
        category_id=category_id,
        service=service,
        contract_id=contract_id,
        contract_version=contract_version,
    )


def test_active_checks_use_public_brokers_without_mutation(tmp_path: Path):
    core_service = FakeService()
    sync = SynchronizationBrokerImpl(core_service)
    runtime = Runtime()
    providers = [
        _resolved("provider.paris", "provider.library", object(), "cifereca.provider"),
        _resolved("provider.gutenberg", "provider.library", object(), "cifereca.provider"),
    ]
    runtime_rows = [
        _resolved(
            "runtime.kobo-adept",
            "runtime.adapter",
            runtime,
            "cifereca.runtime",
            "1.2",
        )
    ]

    def services(category):
        if category == "runtime.adapter":
            return tuple(runtime_rows)
        if category == "provider.library":
            return tuple(providers)
        return ()

    directories = PluginDirectories(
        data=tmp_path / "data",
        cache=tmp_path / "cache",
    )
    context = PluginContext(
        plugin_id="ui.page.diagnostics",
        package_id="org.cifereca.ui.page.diagnostics",
        directories=directories,
        _services_resolver=services,
        _broker_resolver=lambda broker_id: {"sync": sync, "diagnostics": Diagnostics()}.get(broker_id),
    )

    rows = active_checks(context, context.broker("sync"))

    source = next(row for row in rows if row["message"] == "Source Paris Alexandre")
    assert source["status"] == "ok"
    assert source["detail"]["provider_id"] == "paris"
    assert source["detail"]["connected"] is True
    assert source["detail"]["sync_capable"] is True

    runtime_row = next(row for row in rows if row["message"] == "runtime.kobo-adept")
    assert runtime_row["status"] == "ok"
    assert runtime_row["detail"]["contract_version"] == "1.2"
    assert runtime_row["detail"]["resolved_executable"] is True

    provider_row = next(row for row in rows if row["message"] == "Providers chargés")
    assert provider_row["detail"]["count"] == 2

    # Diagnostics are observational only: sources() must not write state and
    # must never call the synchronization operation.
    assert core_service.storage.writes == []
    assert core_service.sync_calls == 0
