"""#5056: phase ownership must be scoped to the source provider."""
from types import SimpleNamespace as NS

from cifereca.brokers import SynchronizationBrokerImpl


class ScopedStorage:
    def __init__(self, owner):
        self.owner = owner
        self.lookups = []

    def identity_for_connection(self, provider, credential):
        self.lookups.append((provider, credential))
        return self.owner


def phase(provider, *, owner="reader-a"):
    storage = ScopedStorage(owner)
    account = NS(provider_id=provider, credential_id="shared-credential",
                 identity_id="reader-a")
    stub = NS(
        service=NS(storage=storage),
        _source_id=lambda _: "sync:synthetic",
        _materialization_state=lambda _: {},
        _safe_phase_timestamp=lambda value: "",
    )
    phases = SynchronizationBrokerImpl._phase_states(
        stub, account, {}, ("local_materialization",)
    )
    return phases[1], storage.lookups


def test_future_provider_phase_does_not_read_kobo_checkpoint():
    # Storage intentionally exposes no metadata_get: reading a Kobo W2 row
    # for a future provider would fail, rather than producing its own phase.
    materialize, lookups = phase("provider.future")
    assert materialize.status == "idle"
    assert lookups == [("provider.future", "shared-credential")]


def test_materialization_phase_refuses_foreign_identity():
    materialize, lookups = phase("provider.future", owner="reader-b")
    assert materialize.status == "unknown"
    assert lookups == [("provider.future", "shared-credential")]
