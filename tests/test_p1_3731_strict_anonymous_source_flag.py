"""#3731: malformed anonymous account flags never grant public-source status.

Provider-neutral, synthetic Config and projected storage only. No plugin
discovery, login, network, synchronization, or persistent configuration write.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from cifereca.config import Config
from cifereca.services import LibraryService


@pytest.mark.parametrize(("flag", "expected"), [
    (True, True), (False, False), (None, False),
    ("false", False), ("true", False),
    (0, False), (1, False), ([], False), ({}, False),
])
def test_anonymous_flag_requires_literal_true_for_setup_and_projection(
    tmp_path, flag, expected,
):
    config = Config(tmp_path / "config.json")
    config.set("providers.accounts", [{
        "id": "source-a", "provider": "future-provider",
        "enabled": True, "anonymous": flag, "credentials": {},
        "identity_id": "default",
    }])
    assert config.has_configured_provider() is expected
    assert config.provider_accounts()[0]["anonymous"] == flag

    connections = []
    host = NS(
        config=config,
        storage=NS(
            identity_put=lambda _identity: None,
            adept_profile_binding_set=lambda *_args: None,
            provider_connection_put=lambda row: connections.append(dict(row)),
        ),
    )
    LibraryService._sync_identity_configuration(host)
    assert len(connections) == 1
    assert connections[0]["enabled"] is True
    assert connections[0]["metadata"]["anonymous"] is expected
    assert config.get("source_sync_consent") is None
    assert not config.path.exists()


def test_missing_legacy_anonymous_flag_remains_false(tmp_path):
    config = Config(tmp_path / "legacy.json")
    config.set("providers.accounts", [{
        "id": "legacy", "provider": "future-provider", "enabled": True,
        "credentials": {}, "identity_id": "default",
    }])
    assert config.has_configured_provider() is False
    storage = []
    host = NS(config=config, storage=NS(
        identity_put=lambda _identity: None,
        adept_profile_binding_set=lambda *_args: None,
        provider_connection_put=lambda row: storage.append(dict(row)),
    ))
    LibraryService._sync_identity_configuration(host)
    assert storage[0]["metadata"]["anonymous"] is False


def test_regular_configured_account_with_credential_is_not_reclassified_as_public(tmp_path):
    config = Config(tmp_path / "credential.json")
    config.set("providers.accounts", [{
        "id": "account", "provider": "future-provider", "enabled": True,
        "anonymous": "true", "credentials": {"username": "synthetic"},
        "identity_id": "default",
    }])
    assert config.has_configured_provider() is True  # credential, not anonymous
    assert config.provider_accounts()[0]["anonymous"] == "true"
