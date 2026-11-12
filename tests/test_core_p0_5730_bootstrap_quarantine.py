"""#5730: descriptor-blind bootstrap and post-attach account cardinality."""
from types import SimpleNamespace as NS

import pytest

from cifereca.config import Config
from cifereca.diagnostics import Diagnostics
from cifereca.providers import ProviderConfigurationError
from cifereca.providers.base import ProviderPlugin, ProviderDescriptor, ProviderCapabilities
from cifereca.services import LibraryService
from cifereca.storage import Storage


class SyntheticProvider(ProviderPlugin):
    def __init__(self, provider_id, limit):
        self.descriptor = ProviderDescriptor(
            id=provider_id, display_name=provider_id,
            description="offline fixture", state="ready",
            capabilities=ProviderCapabilities(),
            max_accounts_per_identity=limit,
        )

    def create_session(self, **kwargs):
        raise AssertionError("validation must never connect a provider")


class SyntheticManager:
    def __init__(self, *providers):
        self.providers = {p.descriptor.id: p for p in providers}

    def candidates(self, category):
        if category == "provider.library":
            return [NS(descriptor=NS(id=k)) for k in self.providers]
        return []

    def service(self, plugin_id):
        return self.providers[plugin_id]

    def subscribe_services(self, category, callback):
        return lambda: None


def host(tmp_path, provider="synthetic", *, identities=("alice",), enabled=True):
    config = Config(tmp_path / "config.json")
    config.set("identities", [{"id": name, "label": name} for name in identities])
    config.set("providers.accounts", [
        {"id": f"cred-{index}", "provider": provider,
         "identity_id": identities[0], "enabled": enabled}
        for index in range(2)
    ])
    storage = Storage(tmp_path / "library.sqlite3")
    return LibraryService(config, storage, Diagnostics()), storage


def test_bootstrap_quarantines_ambiguous_provider_without_deleting_config(tmp_path):
    service, storage = host(tmp_path)
    assert len(service.config.provider_accounts()) == 2
    assert storage.provider_connections() == []
    assert service.registry.descriptors() == []


def test_attached_descriptor_allows_two_credentials_without_kobo_hardcode(tmp_path):
    service, storage = host(tmp_path)
    assert service.attach_plugin_manager(
        SyntheticManager(SyntheticProvider("synthetic", 2))
    ) == ["synthetic"]
    rows = storage.provider_connections()
    assert len(rows) == 2
    assert {row["credential_id"] for row in rows} == {"cred-0", "cred-1"}


def test_limit_one_blocks_registration_and_binding_even_if_disabled(tmp_path):
    service, storage = host(tmp_path, enabled=False)
    with pytest.raises(ProviderConfigurationError, match="limit exceeded") as error:
        service.attach_plugin_manager(
            SyntheticManager(SyntheticProvider("synthetic", 1))
        )
    assert "cred-" not in str(error.value)
    assert service.registry.descriptors() == []
    assert service._plugin_manager is None
    assert storage.provider_connections() == []
    assert len(service.config.provider_accounts()) == 2


def test_unavailable_provider_keeps_ambiguous_scope_quarantined(tmp_path):
    service, storage = host(tmp_path)
    assert service.attach_plugin_manager(SyntheticManager()) == []
    assert storage.provider_connections() == []
    assert len(service.config.provider_accounts()) == 2


def test_descriptor_validation_rejects_second_credential_after_reconfiguration(tmp_path):
    service, storage = host(tmp_path, identities=("alice", "bob"))
    original_manager = SyntheticManager(SyntheticProvider("synthetic", 2))
    service.attach_plugin_manager(original_manager)
    # Narrowed plugin contract must fail before any replacement can be
    # published; the prior provider and persisted rows are preserved.
    with pytest.raises(ProviderConfigurationError, match="limit exceeded"):
        service.attach_plugin_manager(
            SyntheticManager(SyntheticProvider("synthetic", 1))
        )
    assert service.registry.get("synthetic").descriptor.max_accounts_per_identity == 2
    assert service._plugin_manager is original_manager
    assert len(storage.provider_connections()) == 2
