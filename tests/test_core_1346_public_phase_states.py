"""P1 #1346: distinct read-only source phase states and conservative Kobo evidence."""
from __future__ import annotations


from cifereca.plugin_api import SyncPhaseState
from types import SimpleNamespace as NS

from cifereca.brokers import SynchronizationBrokerImpl
from tests.test_core039_contract_surface import Account, FakeService


def _broker(*, broken_owner=False):
    """Isolated synthetic source fixture; no retired D1 preview dependency."""
    service = FakeService()
    service.accounts.append(Account(
        "reader-2", "kobo", "vault-secret", "kobo-reader",
        "PRIVATE KOBO ACCOUNT",
    ))
    service._sessions[service._session_key("kobo", "vault-secret")] = NS(
        connected=True,
        capabilities=NS(
            personal_library=True, backup=True,
            local_materialization=True,
        ),
    )
    service.storage.identity_for_connection = lambda provider, credential: (
        "other-identity" if broken_owner else "reader-2"
    )
    calls = []
    broker = SynchronizationBrokerImpl(service)
    target = next(row for row in broker.sources() if row.provider_id == "kobo")
    return service, broker, target, calls



def _phases(broker, identity="reader-2"):
    source = next(
        item for item in broker.sources(identity) if item.provider_id == "kobo"
    )
    return source, {item.phase: item for item in source.phase_states}


def test_source_phase_contract_does_not_migrate_or_acquire():
    service, broker, _target, calls = _broker()
    source, phases = _phases(broker)
    assert isinstance(phases["discover_rights"], SyncPhaseState)
    assert phases["discover_rights"].status == "idle"
    assert phases["materialize"].status == "idle"
    assert phases["reconcile"].status == "not_supported"
    other = next(item for item in broker.sources("alice") if item.provider_id != "kobo")
    assert {p.phase: p.status for p in other.phase_states} == {
        "discover_rights": "idle", "materialize": "not_supported",
        "reconcile": "not_supported",
    }
    assert calls == [] and service.calls == []
    assert service.storage.values == {}


