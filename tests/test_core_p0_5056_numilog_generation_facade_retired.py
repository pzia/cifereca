"""P0 #5056: legacy Numilog transaction-specific consent facade retired."""
from cifereca.core import source_auto_consent_bridge as bridge


def test_dead_numilog_generation_transaction_facade_stays_retired():
    assert not hasattr(bridge, "numilog_generation_in_transaction")
    assert callable(bridge.consent_generation_in_transaction)
    assert callable(bridge.consent_generation)
    assert not hasattr(bridge, "numilog_generation")
