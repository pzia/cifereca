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



def test_formats_project_all_declared_recipes_without_provider_names():
    service = Service({
        "capabilities": {"download": {
            "synthetic_recipe": {"formats": ["PDF", "CBZ", "pdf"]},
            "http": {"formats": ["EPUB"]},
            "invalid": "untrusted",
        }},
    })

    snapshot = RuntimeCapabilitiesBrokerImpl(service).acquisition("kobo")

    assert snapshot.formats == ("pdf", "cbz", "epub")
    assert snapshot.modes == ()



def test_runtime_snapshot_uses_normalized_requested_provider_not_fixed_label():
    service = Service({"available": True, "compatible": True})

    snapshot = RuntimeCapabilitiesBrokerImpl(service).acquisition(" KOBO ")

    assert snapshot.provider_id == "kobo"
    assert snapshot.available is True
    assert service.calls == [False]


class AttestedService(Service):
    def __init__(self, state):
        super().__init__({"available": True, "kobo_delegated_available": True})
        self.attested_state = state
        self.probes = []

    def runtime_acquisition_state(self, provider_id, *, force=False):
        self.probes.append((provider_id, force))
        return self.attested_state


def test_synthetic_provider_with_core_attestation_has_exact_capabilities():
    service = AttestedService({
        "provider_id": "synthetic", "provider_attested": True,
        "available": True, "compatible": True, "operational": True,
        "contract_version": 2,
        "modes": ["synthetic_delivery", "synthetic_delivery", "adobe_adept"],
        "capabilities": {"download": {"custom": {"formats": ["CBZ", "PDF"]}}},
    })
    snapshot = RuntimeCapabilitiesBrokerImpl(service).acquisition("synthetic", refresh=True)
    assert snapshot == RuntimeAcquisitionCapabilities(
        provider_id="synthetic", available=True, compatible=True,
        operational=True, contract_version=2,
        modes=("synthetic_delivery", "adobe_adept"), formats=("cbz", "pdf"),
    )
    assert service.probes == [("synthetic", True)]
    assert service.calls == []


def test_unattested_malformed_or_foreign_producer_never_falls_back_to_kobo():
    for state in (
        None, {}, {"provider_id": "synthetic", "provider_attested": 1},
        {"provider_id": "foreign", "provider_attested": True, "available": True},
        {"provider_id": "synthetic", "provider_attested": "yes"},
    ):
        service = AttestedService(state)
        for provider in ("synthetic", "kobo"):
            actual = RuntimeCapabilitiesBrokerImpl(service).acquisition(provider)
            assert actual == RuntimeAcquisitionCapabilities(provider_id=provider)
        assert service.calls == []


def test_attested_producer_exception_does_not_probe_other_runtime():
    class FailedProducer(Service):
        def runtime_acquisition_state(self, provider_id, *, force=False):
            raise RuntimeError("sensitive runtime path / secret")

    service = FailedProducer({"available": True, "kobo_delegated_available": True})
    for provider in ("synthetic", "kobo"):
        assert RuntimeCapabilitiesBrokerImpl(service).acquisition(
            provider, refresh=True,
        ) == RuntimeAcquisitionCapabilities(provider_id=provider)
    assert service.calls == []


def test_attested_producer_property_exception_fails_closed():
    class FailedProperty(Service):
        @property
        def runtime_acquisition_state(self):
            raise RuntimeError("sensitive runtime path / secret")

    service = FailedProperty({"available": True})
    assert RuntimeCapabilitiesBrokerImpl(service).acquisition("kobo") == (
        RuntimeAcquisitionCapabilities(provider_id="kobo")
    )
    assert service.calls == []


def test_attested_producer_must_publish_modes_not_legacy_vendor_flags():
    service = AttestedService({
        "provider_id": "synthetic", "provider_attested": True,
        "available": True, "compatible": True,
        "kobo_delegated_available": True,
    })
    snapshot = RuntimeCapabilitiesBrokerImpl(service).acquisition("synthetic")
    assert snapshot.available is True
    assert snapshot.modes == ()
    assert service.calls == []


def test_other_runtime_provider_never_inherits_kobo_capabilities():
    service = Service({
        "available": True, "compatible": True, "operational": True,
        "kobo_delegated_available": True,
    })

    snapshot = RuntimeCapabilitiesBrokerImpl(service).acquisition("synthetic")

    assert snapshot == RuntimeAcquisitionCapabilities(provider_id="synthetic")
    assert service.calls == []
