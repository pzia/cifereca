"""#3196: generic source preflight cannot infer delivery from legacy metadata."""
from __future__ import annotations

from types import SimpleNamespace as NS

from cifereca.brokers import SynchronizationBrokerImpl
from cifereca.gui.sync_view import sync_local_preflight_presentation
from cifereca.plugin_api import SyncResult
from tests.test_core039_contract_surface import Account, FakeService


def test_unknown_provider_preflight_fails_closed_without_network():
    service = FakeService()
    service.accounts.append(Account(
        "reader-2", "synthetic", "credential", "synthetic-reader",
        "Synthetic account",
    ))
    broker = SynchronizationBrokerImpl(service)
    target = next(row for row in broker.sources() if row.provider_id == "synthetic")
    report = broker.preview_local_materialization(target.source_id)
    assert report.ok is False
    assert report.facts["status"] == "not_supported"
    assert report.facts["acquisition_authorized"] is False
    assert service.calls == [] and service.storage.values == {}


def test_generic_preflight_invalid_scope_never_authorizes():
    broker = SynchronizationBrokerImpl(FakeService())
    report = broker.preview_local_materialization("unregistered-source")
    assert not report.ok
    assert report.facts["status"] == "unknown_source"
    assert report.facts["acquisition_authorized"] is False


def test_preflight_rendering_drops_provider_secrets_and_untrusted_counters():
    bad = SyncResult(
        ok=True, facts={
            "status": "preview_only",
            "counts": {"already_local_linked": "PRIVATE_TOKEN"},
            "known_item_count": 1,
            "read_only": True,
            "acquisition_authorized": False,
        },
    )
    text = sync_local_preflight_presentation(bad)
    assert text.startswith("Aperçu indisponible.")
    assert "PRIVATE" not in text
