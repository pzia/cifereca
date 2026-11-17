"""#5077: frozen data formats, no provider-specific authorization dispatch."""
import pytest

from cifereca.core import source_auto_consent_bridge as bridge
from cifereca.core import legacy_source_consent_metadata as metadata
from cifereca.core.source_convergence import SourceKey
from cifereca.core.source_sync_consent import read_policy
from cifereca.plugin_api.provider_rights_observation import ProviderRightsScope
from cifereca.storage import Storage


@pytest.mark.parametrize("provider", ["numilog", "kobo", "bnf-pret", "fictional"])
@pytest.mark.parametrize("index", [0, 1, 2])
def test_same_historical_formats_for_every_exact_provider_scope(tmp_path, provider, index):
    scope = ProviderRightsScope("alice", provider, "credential", "account")
    other = ProviderRightsScope("alice", provider, "other", "account")
    key, old_on, revision = metadata.historical_consent_keys(scope)[index]
    kind = "loan" if index == 1 else "purchase"
    source = SourceKey(scope, "edition", "right", kind, "branch")
    foreign = SourceKey(other, "edition", "right", kind, "branch")
    db = Storage(tmp_path / "historical.sqlite")
    try:
        db.metadata_set(key, old_on)
        db.metadata_set(revision, "a" * 32)
        assert read_policy(db, scope).mode == "unset"
        assert bridge.consent_generation(db, scope) == "a" * 32
        assert bridge.consent_generation(db, other) is None
        with db._lock, db._connect() as connection:
            assert bridge.toggle_state(connection, source) == "off"
            assert bridge.toggle_state(connection, foreign) is None
            assert bridge.consent_generation_in_transaction(connection, source) == "a" * 32
        remainders = [epoch for _key, _on, epoch in metadata.historical_consent_keys(scope)
                      if epoch != revision]
        db.metadata_set(remainders[0], "b" * 32)
        assert bridge.consent_generation(db, scope) == "invalid"
        with db._lock, db._connect() as connection:
            assert bridge.consent_generation_in_transaction(connection, source) == "invalid"
    finally:
        db.close()


def test_no_supplier_policy_lookup_survives():
    assert not hasattr(metadata, "_POLICIES")
    assert not hasattr(bridge, "_POLICIES")
    assert not hasattr(metadata, "_consent_generation_key")
