from __future__ import annotations

from cifereca.brokers import RuntimeCapabilitiesBrokerImpl, build_public_brokers
from cifereca.plugin_api import (
    RuntimeAcquisitionCapabilities,
    RuntimeCapabilitiesBroker,
)


class Service:
    def __init__(self, state):
        self.state = state
        self.calls = []

    def kobo_adept_state(self, *, force=False):
        self.calls.append(force)
        return dict(self.state)


def test_public_registry_exposes_runtime_capabilities_broker():
    broker = build_public_brokers(Service({}))["runtime.capabilities"]

    assert isinstance(broker, RuntimeCapabilitiesBroker)


def test_kobo_modes_are_derived_from_published_capabilities_not_contract_version_only():
    service = Service({
        "available": True,
        "compatible": True,
        "operational": True,
        "contract_version": 8,
        "software_version": "0.13.2",
        "declared_capabilities": ["download_http_epub_pdf"],
        "capabilities": {
            "download": {
                "http": {"available": False, "formats": ["EPUB"]},
                "acsm": {"available": True, "formats": ["EPUB", "PDF"]},
                "kobo": {"available": True, "formats": ["KEPUB"]},
            }
        },
        "acsm_available": True,
        "kobo_delegated_available": True,
        "kobo_kdrm_available": False,
        "kobo_formats": ["kepub"],
    })

    snapshot = RuntimeCapabilitiesBrokerImpl(service).acquisition("kobo")

    assert snapshot == RuntimeAcquisitionCapabilities(
        provider_id="kobo",
        available=True,
        compatible=True,
        operational=True,
        contract_version=8,
        modes=("http_plain", "adobe_adept", "kobo_delegated"),
        formats=("epub", "pdf", "kepub"),
        software_version="0.13.2",
    )


def test_contract_compatibility_alone_does_not_invent_acquisition_modes():
    service = Service({
        "available": True,
        "compatible": True,
        "operational": True,
        "contract_version": 8,
        "capabilities": {},
        "declared_capabilities": [],
        "acsm_available": False,
        "kobo_delegated_available": False,
        "kobo_kdrm_available": False,
    })

    snapshot = RuntimeCapabilitiesBrokerImpl(service).acquisition("kobo")

    assert snapshot.compatible is True
    assert snapshot.contract_version == 8
    assert snapshot.modes == ()


def test_explicit_refresh_is_forwarded_to_runtime_probe():
    service = Service({"contract_version": 8})

    RuntimeCapabilitiesBrokerImpl(service).acquisition("kobo", refresh=True)

    assert service.calls == [True]


def test_unknown_provider_returns_empty_bounded_snapshot_without_runtime_probe():
    service = Service({"available": True})

    snapshot = RuntimeCapabilitiesBrokerImpl(service).acquisition("other")

    assert snapshot == RuntimeAcquisitionCapabilities(provider_id="other")
    assert service.calls == []


def test_snapshot_has_no_raw_runtime_payload_or_secret_fields():
    service = Service({
        "available": True,
        "compatible": True,
        "operational": True,
        "contract_version": 8,
        "software_version": "v" * 200,
        "capabilities": {"secret": "TOKEN", "download": {}},
        "declared_capabilities": [],
        "payload": {"token": "TOKEN"},
        "stdout": "TOKEN",
        "stderr": "TOKEN",
    })

    snapshot = RuntimeCapabilitiesBrokerImpl(service).acquisition("kobo")

    assert len(snapshot.software_version) == 80
    assert not hasattr(snapshot, "payload")
    assert not hasattr(snapshot, "stdout")
    assert not hasattr(snapshot, "stderr")
