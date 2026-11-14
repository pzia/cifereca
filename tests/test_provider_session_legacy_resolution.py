"""Regression guards for unambiguous legacy source-session lookup (#5851)."""

from types import SimpleNamespace

import pytest

from cifereca.core.provider_sessions import _session_for_source
from cifereca.providers import ProviderConfigurationError


def _facade(*sessions):
    return SimpleNamespace(
        _sessions={(session.provider_id, session.credential_id): session for session in sessions},
        _session_key=lambda provider, credential: (provider, credential),
        _provider_enabled_for_identity=lambda provider: True,
    )


def _session(provider, credential, connected=True):
    return SimpleNamespace(
        provider_id=provider, credential_id=credential, connected=connected,
    )


def test_source_session_lookup_requires_a_credential_even_with_one_live_session():
    first = _session("synthetic", "alice")
    second = _session("synthetic", "bob")
    facade = _facade(first, second)

    with pytest.raises(TypeError):
        _session_for_source(facade, "synthetic")
    assert _session_for_source(facade, "synthetic", "alice") is first
    assert _session_for_source(facade, "synthetic", "bob") is second

    second.connected = False
    with pytest.raises(TypeError):
        _session_for_source(facade, "synthetic")


def test_explicit_credential_is_never_replaced_by_only_remaining_live_session():
    first = _session("synthetic", "alice", connected=False)
    second = _session("synthetic", "bob")
    facade = _facade(first, second)
    assert _session_for_source(facade, "synthetic", "alice") is first
    assert _session_for_source(facade, "synthetic", "bob") is second
    with pytest.raises(TypeError):
        _session_for_source(facade, "synthetic")


def test_explicit_disconnected_credential_never_falls_back():
    first = _session("synthetic", "alice", connected=False)
    second = _session("synthetic", "bob")
    facade = _facade(first, second)

    # Existing exact-coordinate behavior is preserved: the caller must
    # separately verify connected state, never substitute a different account.
    assert _session_for_source(facade, "synthetic", "alice") is first
    with pytest.raises(ProviderConfigurationError):
        _session_for_source(facade, "synthetic", "unknown")
