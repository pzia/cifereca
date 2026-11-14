"""#5975: generic DRM recipe dispatch is bound by reciprocal installed manifests.

No network, user secrets, provider CLI, filesystem changes or historical
backup writer. An unrecognized source does not choose any runtime service.
"""
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from cifereca.core.provider_sessions import runtime_acquisition_binding
from cifereca.plugin_api.models import PluginDescriptor
from cifereca.providers import ProviderConfigurationError
from cifereca.services import LibraryService


def _rig(*, provider="fictional-source"):
    provider_plugin = "provider." + provider
    runtime_plugin = "runtime.fictional-adapter"
    provider_descriptor = PluginDescriptor(
        id=provider_plugin, type="provider.library",
        version="1.0", package_id="pkg.test", package_version="1.0",
        runtime_acquisition_service=runtime_plugin,
    )
    runtime_descriptor = PluginDescriptor(
        id=runtime_plugin, type="runtime.adapter",
        version="1.0", package_id="pkg.runtime", package_version="1.0",
        runtime_acquisition_providers=(provider,),
        capabilities=("acquisition.fulfill",),
    )
    status = {
        provider_plugin: NS(descriptor=provider_descriptor, enabled=True, state="ready"),
        runtime_plugin: NS(descriptor=runtime_descriptor, enabled=True, state="ready"),
    }
    calls = []
    plugin = NS(download_request=lambda request: (
        calls.append(("execute", request)) or {"ok": True, "artifact": {}}
    ))
    services = {runtime_plugin: plugin}

    def get_service(plugin_id):
        calls.append(("service", plugin_id))
        return services.get(plugin_id)
    manager = NS(
        status=lambda plugin_id: status[plugin_id],
        service=get_service, registry_generation=1,
    )
    core = NS(
        registry=NS(maybe_get=lambda logical: (
            NS(descriptor=NS(id=provider)) if logical == provider else None
        )),
        _provider_plugin_ids={provider: provider_plugin},
        _plugin_manager=manager,
    )
    core.runtime_acquisition_binding = (
        lambda logical: runtime_acquisition_binding(core, logical)
    )
    core._required_plugin_service = (
        lambda plugin_id: LibraryService._required_plugin_service(core, plugin_id)
    )
    recipe = {
        "schema_version": 2,
        "source": {"type": provider, "runtime_service": "runtime.attacker",
                   "provider": "attacker"},
        "destination": "/tmp/placeholder-only.epub",
    }
    return core, recipe, calls, status, services, runtime_plugin


def test_generic_source_uses_only_its_reciprocal_installed_runtime():
    core, request, calls, _, _, runtime_id = _rig()
    result = LibraryService.execute_acquisition_request(core, request)
    assert result["ok"] is True
    assert calls == [
        ("service", runtime_id), ("execute", request), ("service", runtime_id),
    ]


@pytest.mark.parametrize("break_binding", (
    "source_disabled", "runtime_disabled", "source_wrong_type",
    "runtime_wrong_type", "different_runtime_claim", "no_source_claim",
    "unknown_source", "runtime_unready",
))
def test_missing_disabled_or_foreign_claim_cannot_execute(break_binding):
    core, request, calls, statuses, _, runtime_id = _rig()
    source_id = core._provider_plugin_ids["fictional-source"]
    if break_binding == "source_disabled":
        statuses[source_id].enabled = False
    elif break_binding == "runtime_disabled":
        statuses[runtime_id].enabled = False
    elif break_binding == "source_wrong_type":
        statuses[source_id].descriptor = replace(
            statuses[source_id].descriptor, type="ui.page"
        )
    elif break_binding == "runtime_wrong_type":
        statuses[runtime_id].descriptor = replace(
            statuses[runtime_id].descriptor, type="provider.library"
        )
    elif break_binding == "different_runtime_claim":
        statuses[runtime_id].descriptor = replace(
            statuses[runtime_id].descriptor, runtime_acquisition_providers=("other",)
        )
    elif break_binding == "no_source_claim":
        statuses[source_id].descriptor = replace(
            statuses[source_id].descriptor, runtime_acquisition_service=None
        )
    elif break_binding == "unknown_source":
        request["source"]["type"] = "missing"
    elif break_binding == "runtime_unready":
        statuses[runtime_id].state = "failed"
    with pytest.raises(ProviderConfigurationError, match="non supporté"):
        LibraryService.execute_acquisition_request(core, request)
    assert calls == []


def test_rebound_producer_during_download_rejects_its_result():
    core, request, calls, _, services, runtime_id = _rig()
    def rebinding(_request):
        calls.append(("execute", request))
        services[runtime_id] = NS(download_request=lambda *_: pytest.fail(
            "never execute the new producer"
        ))
        return {"ok": True, "artifact": {}}
    services[runtime_id].download_request = rebinding
    with pytest.raises(ProviderConfigurationError, match="révoqué pendant"):
        LibraryService.execute_acquisition_request(core, request)
    assert calls == [
        ("service", runtime_id), ("execute", request), ("service", runtime_id),
    ]


def test_aba_of_registry_epoch_rejects_even_same_producer_and_binding():
    core, request, calls, _, services, runtime_id = _rig()
    def replace_then_restore(_request):
        calls.append(("execute", request))
        core._plugin_manager.registry_generation += 2
        return {"ok": True, "artifact": {}}
    services[runtime_id].download_request = replace_then_restore
    with pytest.raises(ProviderConfigurationError, match="révoqué pendant"):
        LibraryService.execute_acquisition_request(core, request)
    assert sum(event[0] == "execute" for event in calls) == 1


def test_missing_runtime_method_refuses_without_invoking_any_fallback():
    core, request, calls, _, services, runtime_id = _rig()
    services[runtime_id] = NS()
    with pytest.raises(ProviderConfigurationError, match="sans contrat"):
        LibraryService.execute_acquisition_request(core, request)
    assert calls == [("service", runtime_id)]
