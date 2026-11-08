"""P0 #5056: legacy Numilog transaction-specific consent facade retired."""
from cifereca.core import source_auto_consent_bridge as bridge


def test_dead_numilog_generation_transaction_facade_stays_retired():
    assert not hasattr(bridge, "numilog_generation_in_transaction")
    assert callable(bridge.consent_generation_in_transaction)
    assert callable(bridge.consent_generation)
    assert not hasattr(bridge, "numilog_generation")


def test_generic_consent_generation_reads_exact_legacy_numilog_epoch():
    from cifereca.plugin_api.provider_rights_observation import ProviderRightsScope

    scope = ProviderRightsScope("alice", "numilog", "credential-a", "")
    foreign = ProviderRightsScope("alice", "numilog", "credential-b", "")
    key = bridge._consent_generation_key(scope)
    assert key is not None and key != bridge._consent_generation_key(foreign)

    class MemoryMetadata:
        def __init__(self):
            self.rows = {}

        def metadata_get(self, name):
            return self.rows.get(name)

    storage = MemoryMetadata()
    first = "a" * 32
    second = "b" * 32
    assert bridge.consent_generation(storage, scope) is None
    storage.rows[key] = first
    assert bridge.consent_generation(storage, scope) == first
    assert bridge.consent_generation(storage, foreign) is None
    # Revocation/re-opt-in cannot revive an in-flight first-epoch delivery.
    storage.rows[key] = second
    assert bridge.consent_generation(storage, scope) == second
    assert bridge.consent_generation(storage, scope) != first
    storage.rows[key] = "invalid"
    assert bridge.consent_generation(storage, scope) == "invalid"
