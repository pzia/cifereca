"""P0 #5851 — Core producer never confuses a runtime capability with ownership."""
from __future__ import annotations

from types import SimpleNamespace

from cifereca.brokers import RuntimeCapabilitiesBrokerImpl
from cifereca.plugin_api import RuntimeAcquisitionCapabilities
from cifereca.services import LibraryService


class FakeManager:
    def __init__(self, runtime):
        self.runtime = runtime
        self.calls = []

    def service(self, runtime_id):
        self.calls.append(runtime_id)
        assert runtime_id == "runtime.synthetic"
        return self.runtime


class FakeService:
    runtime_acquisition_state = LibraryService.runtime_acquisition_state

    def __init__(self, binding="runtime.synthetic", reported=None, *, failure=False):
        self.binding = binding
        self.probes = []
        self.runtime = SimpleNamespace()
        if not failure:
            self.runtime.acquisition_capabilities = self._producer
        self.reported = reported
        self._plugin_manager = FakeManager(self.runtime)

    def runtime_acquisition_binding(self, provider_id):
        self.probes.append(("binding", provider_id))
        return self.binding if provider_id == "synthetic" else None

    def _producer(self, *, force=False):
        self.probes.append(("producer", force))
        return self.reported


def test_attested_real_core_producer_has_exact_provider_provenance():
    service = FakeService(reported={
        "available": True, "compatible": True, "operational": True,
        "contract_version": 8,
        "modes": ["synthetic_delivery"], "formats": ["CBZ"],
        "stdout": "PRIVATE", "resolved_executable": "/secret/path",
        "provider_id": "foreign", "provider_attested": False,
    })
    snapshot = RuntimeCapabilitiesBrokerImpl(service).acquisition(
        "synthetic", refresh=True,
    )
    assert snapshot == RuntimeAcquisitionCapabilities(
        provider_id="synthetic", available=True, compatible=True,
        operational=True, contract_version=8,
        modes=("synthetic_delivery",), formats=("cbz",),
    )
    # The post-probe check fences revocation/rebinding during runtime I/O.
    assert service.probes == [
        ("binding", "synthetic"), ("producer", True),
        ("binding", "synthetic"),
    ]
    assert service._plugin_manager.calls == ["runtime.synthetic"]
    assert not hasattr(snapshot, "stdout")


def test_unbound_unknown_and_missing_runtime_method_never_probe():
    service = FakeService(binding=None, reported={"available": True})
    assert LibraryService.runtime_acquisition_state(service, "synthetic") == {}
    assert service._plugin_manager.calls == []
    service = FakeService(reported={"available": True})
    assert RuntimeCapabilitiesBrokerImpl(service).acquisition("other") == (
        RuntimeAcquisitionCapabilities(provider_id="other")
    )
    assert service._plugin_manager.calls == []
    service = FakeService(reported={"available": True}, failure=True)
    assert LibraryService.runtime_acquisition_state(service, "synthetic") == {}
    assert service._plugin_manager.calls == ["runtime.synthetic"]


def test_malformed_hostile_or_throwing_runtime_fails_closed():
    class Hostile:
        def __str__(self):
            raise AssertionError("never serialize plugin objects")

    for payload in (None, "forged", [1, 2], Hostile()):
        service = FakeService(reported=payload)
        assert RuntimeCapabilitiesBrokerImpl(service).acquisition("synthetic") == (
            RuntimeAcquisitionCapabilities(provider_id="synthetic")
        )

    service = FakeService(reported={})
    def explode(*, force=False):
        raise RuntimeError("sensitive CLI stderr")
    service.runtime.acquisition_capabilities = explode
    assert RuntimeCapabilitiesBrokerImpl(service).acquisition("synthetic") == (
        RuntimeAcquisitionCapabilities(provider_id="synthetic")
    )
