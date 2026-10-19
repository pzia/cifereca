"""P0 #1143: health checks do not export plugin-controlled exception class names.

The tests construct DiagnosticsBroker with fake providers. No GUI, provider
request, plugin installation or external command is used.
"""
from __future__ import annotations

import json
from types import SimpleNamespace as NS

from cifereca.brokers import DiagnosticsBrokerImpl
from tests.test_diagnostics_health_contract import Service


PRIVATE = "PRIVATE_ACCOUNT_DIAGNOSTIC_TOKEN"


class SecretClassName(RuntimeError):
    def __str__(self):
        raise AssertionError("Do not stringify this private plugin exception")

    def __repr__(self):
        raise AssertionError("Do not repr this private plugin exception")


SecretClassName.__name__ = PRIVATE


def _throw(*_args, **_kwargs):
    raise SecretClassName(PRIVATE)


def test_failed_provider_and_check_inventory_export_only_known_reasons():
    service = Service()
    service.plugin_manager = NS(services=_throw)
    rows = DiagnosticsBrokerImpl(service).active_checks()
    relevant = [row for row in rows
                if row.check_id in {"core.providers.loaded", "core.diagnostics.contributions"}]
    assert len(relevant) == 2
    assert relevant[0].detail == {"code": "providers_unavailable"}
    assert relevant[1].detail == {"code": "diagnostic_contributions_unavailable"}
    assert PRIVATE not in json.dumps([row.detail for row in rows])


def test_bad_plugin_check_and_invalid_result_are_not_reflected_in_diagnostics():
    class Provider:
        def checks(self):
            return [1, object()]
    class Broken:
        def checks(self):
            raise SecretClassName(PRIVATE)
    service = Service()
    service.plugin_manager = NS(services=lambda category: (
        [NS(plugin_id="public.ok", service=Provider()),
         NS(plugin_id="public.fail", service=Broken())]
        if category == "diagnostics.check" else []
    ))
    rows = DiagnosticsBrokerImpl(service).active_checks()
    bad = [row for row in rows if row.component == "public.fail"]
    assert len(bad) == 1
    assert bad[0].status == "error"
    assert bad[0].detail == {"code": "diagnostic_check_failed"}
    invalid = [row for row in rows if row.message == "Résultat de diagnostic invalide"]
    assert len(invalid) == 2
    assert all(row.detail == {"code": "invalid_diagnostic_result"} for row in invalid)
    assert PRIVATE not in json.dumps([row.detail for row in rows])


def test_sync_source_enumeration_failure_does_not_export_exception_type():
    service = Service()
    service.accounts_for_identity = _throw
    rows = DiagnosticsBrokerImpl(service).active_checks()
    [source] = [row for row in rows if row.check_id == "core.sync.sources"]
    assert source.status == "error"
    assert source.detail == {"code": "sync_sources_unavailable"}
    assert PRIVATE not in json.dumps([row.detail for row in rows])
