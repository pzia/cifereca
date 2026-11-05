"""#3731: only native bool True may activate a configured provider account.

The legacy absence of "enabled" remains enabled for compatibility. Malformed
explicit flags never authorize connection, source discovery or SQLite status.
Synthetic config and storage only; no HTTP/plugin/credential access.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from cifereca.config import Config
from cifereca.services import LibraryService


@pytest.mark.parametrize(("flag", "expected"), [
    (True, True), (False, False), ("false", False),
    ("true", False), (1, False), (0, False),
    (None, False), ([], False), ({}, False),
])
def test_explicit_provider_activation_flag_is_not_truthy_coercion(tmp_path, flag, expected):
    cfg = Config(tmp_path / "settings.json")
    cfg.set("providers.accounts", [{
        "provider": "unknown-provider", "id": "source-1",
        "identity_id": "default", "enabled": flag,
        "credentials": {"username": "synthetic-user"},
    }])
    eligible = cfg.provider_accounts(enabled_only=True)
    assert len(eligible) == int(expected)
    assert cfg.has_configured_provider() is expected
    assert cfg.provider_accounts()[0]["enabled"] == flag
    assert cfg.get("source_sync_consent") is None


def test_missing_legacy_provider_flag_remains_enabled_without_new_consent(tmp_path):
    cfg = Config(tmp_path / "legacy.json")
    cfg.set("providers.accounts", [{
        "provider": "unknown-provider", "id": "legacy-1",
        "identity_id": "default", "credentials": {"username": "old-user"},
    }])
    assert [row["id"] for row in cfg.provider_accounts(enabled_only=True)] == [
        "legacy-1",
    ]
    assert cfg.has_configured_provider() is True
    assert cfg.get("source_sync_consent") is None


@pytest.mark.parametrize(("flag", "expected"), [
    ("false", False), ("true", False), (1, False),
    (None, False), (True, True), (False, False),
])
def test_core_identity_projection_never_promotes_malformed_enabled(tmp_path, flag, expected):
    cfg = Config(tmp_path / "storage-settings.json")
    cfg.set("providers.accounts", [{
        "provider": "unknown-provider", "id": "source-1",
        "identity_id": "default", "enabled": flag, "credentials": {},
    }])
    connections = []
    storage = NS(
        identity_put=lambda _identity: None,
        adept_profile_binding_set=lambda *_args: None,
        provider_connection_put=lambda row: connections.append(dict(row)),
    )
    host = NS(config=cfg, storage=storage)
    LibraryService._sync_identity_configuration(host)
    assert len(connections) == 1
    assert connections[0]["provider_id"] == "unknown-provider"
    assert connections[0]["enabled"] is expected
    assert cfg.get("source_sync_consent") is None


def test_core_projection_preserves_legacy_missing_enabled(tmp_path):
    cfg = Config(tmp_path / "missing-flag.json")
    cfg.set("providers.accounts", [{
        "provider": "unknown-provider", "id": "legacy-1",
        "identity_id": "default", "credentials": {},
    }])
    connections = []
    host = NS(
        config=cfg,
        storage=NS(
            identity_put=lambda _identity: None,
            adept_profile_binding_set=lambda *_args: None,
            provider_connection_put=lambda row: connections.append(dict(row)),
        ),
    )
    LibraryService._sync_identity_configuration(host)
    assert [row["enabled"] for row in connections] == [True]
